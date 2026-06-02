import os, json, threading, time
from flask import Flask, render_template, jsonify, request
from substrateinterface import SubstrateInterface

app = Flask(__name__)
VALIDATOR_URL = os.environ.get("QUIP_VALIDATOR_URL", "http://localhost:20049/rpc")
CACHE_DIR = os.environ.get("CACHE_DIR", "/tmp/cache")
_running_scans = set()

def get_timestamp(substrate, block_num):
    try:
        result = substrate.query("Timestamp", "Now", block_hash=substrate.get_block_hash(block_num))
        if result and result.value:
            return int(result.value) // 1000
    except:
        pass
    return None

def get_extrinsic_index(event_val):
    phase = event_val.get("phase", {})
    if isinstance(phase, dict):
        for key in ("apply_extrinsic", "initialization", "finalization"):
            v = phase.get(key)
            if v is not None:
                return v
    if isinstance(phase, (int, float)):
        return int(phase)
    return -1

def get_events_for_block(substrate, block_num, address):
    results = []
    try:
        block_hash = substrate.get_block_hash(block_num)
        events = substrate.get_events(block_hash)
        ts = get_timestamp(substrate, block_num)
        for event in events:
            val = event.value
            mod = val.get("module_id", "")
            evt = val.get("event_id", "")
            attrs = val.get("attributes") or val.get("event", {}).get("attributes", {})
            attrs_lower = {k: str(v).lower() for k, v in attrs.items()}
            if not any(address.lower() in v for v in attrs_lower.values()):
                continue
            amount = None
            for ak in ("amount", "free_balance", "reward", "value"):
                if ak in attrs:
                    try: amount = int(attrs[ak]) / 10**12
                    except: pass
                    break
            ext_idx = get_extrinsic_index(val)
            r = {"block": block_num, "extrinsic_index": ext_idx, "ts": ts, "type": "info", "subtype": "info", "amount": amount, "counterparty": None, "counterparty_role": None, "raw_module": mod, "raw_event": evt, "attributes": {k: str(v) for k, v in attrs.items()}}
            if mod == "Balances" and evt == "Transfer":
                src = str(attrs.get("from", ""))
                dst = str(attrs.get("to", ""))
                if address.lower() in src.lower():
                    r["type"] = "transfer"; r["subtype"] = "outgoing"; r["counterparty"] = dst; r["counterparty_role"] = "to"
                else:
                    r["type"] = "transfer"; r["subtype"] = "incoming"; r["counterparty"] = src; r["counterparty_role"] = "from"
            elif mod == "Balances" and evt == "Endowed":
                r["type"] = "faucet"; r["subtype"] = "incoming"; r["counterparty"] = "System"
            elif mod == "Balances" and evt == "Deposit":
                r["type"] = "deposit"; r["subtype"] = "incoming"
            elif mod == "Balances" and evt == "Withdraw":
                r["type"] = "fee"; r["subtype"] = "outgoing"
            elif mod == "QuantumPow" and evt == "ProofAccepted":
                r["type"] = "mining"; r["subtype"] = "incoming"
            elif mod == "QuantumPow" and evt == "BlockWinner":
                r["type"] = "block_winner"; r["subtype"] = "incoming"
            elif mod == "FaucetOps" and evt == "Minted":
                r["type"] = "faucet"; r["subtype"] = "incoming"
                who = str(attrs.get("who", ""))
                if who and address.lower() not in who.lower():
                    r["counterparty"] = who; r["counterparty_role"] = "who"
            elif mod == "System" and evt == "Remarked":
                r["type"] = "remark"; r["subtype"] = "info"
            else:
                r["type"] = mod + "." + evt
                for who_key in ("who", "address", "account", "sender", "target", "controller"):
                    v = str(attrs.get(who_key, ""))
                    if v and address.lower() not in v.lower():
                        r["counterparty"] = v; r["counterparty_role"] = who_key
                        break
            results.append(r)
    except:
        pass
    return results

def group_events_into_transactions(raw_events):
    groups = {}
    for ev in raw_events:
        ext_idx = ev.get("extrinsic_index", -1)
        if ext_idx is None: ext_idx = -1
        key = (ev["block"], ext_idx)
        if key not in groups:
            groups[key] = []
        groups[key].append(ev)

    txs = []
    type_order = {"transfer": 0, "faucet": 1, "block_winner": 2, "mining": 3, "deposit": 4, "fee": 5, "remark": 6}
    for (block, ext_idx), evts in groups.items():
        net = 0.0
        has_balance_change = False
        main_type = "info"
        main_subtype = "info"
        cp = None
        cp_role = None
        sub = []

        for ev in evts:
            raw_mod = ev.get("raw_module", ev.get("type", "").split(".")[0] if "." in ev.get("type", "") else ev.get("type", ""))
            raw_evt = ev.get("raw_event", "")
            sub.append({"module_id": raw_mod, "event_id": raw_evt, "attributes": ev.get("attributes", {})})
            m = raw_mod; e = raw_evt
            if m == "Balances" and e == "Deposit":
                amt = ev.get("amount") or 0
                net += abs(amt); has_balance_change = True
            elif m == "Balances" and e == "Withdraw":
                amt = ev.get("amount") or 0
                net -= abs(amt); has_balance_change = True
            elif m == "Balances" and e == "Transfer":
                amt = ev.get("amount") or 0
                if ev["subtype"] == "incoming": net += abs(amt)
                else: net -= abs(amt)
                has_balance_change = True

            t = ev["type"]
            p = type_order.get(t, 99)
            if p < type_order.get(main_type, 99):
                main_type = t
                main_subtype = ev.get("subtype", "info")
                if ev.get("counterparty"):
                    cp = ev["counterparty"]; cp_role = ev["counterparty_role"]

        if has_balance_change:
            main_subtype = "incoming" if net > 0 else "outgoing" if net < 0 else "info"
        net_rounded = round(net, 4) if has_balance_change else None

        txs.append({
            "block": block,
            "extrinsic_index": ext_idx,
            "ts": evts[0]["ts"],
            "type": main_type,
            "subtype": main_subtype,
            "amount": net_rounded,
            "counterparty": cp,
            "counterparty_role": cp_role,
            "sub_events": sub,
        })
    return txs

def cache_path(address):
    return os.path.join(CACHE_DIR, f"{address}.json")

def load_cache(address):
    p = cache_path(address)
    if os.path.exists(p):
        with open(p) as f:
            return json.load(f)
    return {}

def save_cache(address, data):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cache_path(address), "w") as f:
        json.dump(data, f)

def scan_full_history(address, latest_block):
    global _running_scans
    try:
        cache = load_cache(address)
        last_scanned = cache.get("last_scanned", 0)
        start_from = last_scanned + 1
        if start_from <= 0:
            start_from = 1

        if start_from > latest_block:
            _running_scans.discard(address)
            return

        all_raw = cache.get("events", [])
        all_txs = cache.get("transactions", [])
        if all_raw and not all_txs:
            all_txs = group_events_into_transactions(all_raw)
            save_cache(address, {**cache, "transactions": all_txs})
        total = latest_block - start_from + 1
        substrate = SubstrateInterface(url=VALIDATOR_URL)
        try:
            for bn in range(start_from, latest_block + 1):
                try:
                    e = get_events_for_block(substrate, bn, address)
                    if e:
                        all_raw.extend(e)
                        new_txs = group_events_into_transactions(e)
                        all_txs.extend(new_txs)
                except:
                    pass
                if bn % 100 == 0 or bn == latest_block:
                    scanned = bn - start_from + 1
                    pct = round(scanned / total * 100, 1) if total > 0 else 100
                    save_cache(address, {
                        "status": "scanning" if bn < latest_block else "complete",
                        "progress": pct,
                        "last_scanned": bn,
                        "scanned": scanned,
                        "total": total,
                        "events": all_raw,
                        "transactions": all_txs,
                    })
        finally:
            substrate.close()
    except Exception as e:
        save_cache(address, {"status": "error", "error": str(e), "last_scanned": cache.get("last_scanned", 0), "events": all_raw if "all_raw" in dir() else [], "transactions": all_txs if "all_txs" in dir() else []})
    finally:
        _running_scans.discard(address)

def decode_params(params):
    if isinstance(params, list):
        return [decode_params(p) for p in params]
    if isinstance(params, dict):
        return {k: decode_params(v) for k, v in params.items()}
    if hasattr(params, "value"):
        return params.value
    return str(params)

def get_block_data(substrate, block_num):
    try:
        block_hash = substrate.get_block_hash(block_num)
        block = substrate.get_block(block_hash)
        header = block["header"]
        extrinsics = block.get("extrinsics", [])
        events = substrate.get_events(block_hash)
        ts = get_timestamp(substrate, block_num)
        author = substrate.get_block_author(block_hash) if hasattr(substrate, "get_block_author") else None
        ext_list = []
        for i, ext in enumerate(extrinsics):
            val = ext.value if hasattr(ext, "value") else ext
            ext_list.append({
                "index": i,
                "signer": val.get("address", ""),
                "call_module": val.get("call_module", ""),
                "call_function": val.get("call_function", ""),
                "params": decode_params(val.get("params", [])),
                "hash": str(ext.extrinsic_hash) if hasattr(ext, "extrinsic_hash") else None,
                "success": None,
            })
        event_list = []
        for ev in events:
            val = ev.value if hasattr(ev, "value") else ev
            attrs = val.get("attributes", {}) or val.get("event", {}).get("attributes", {})
            event_list.append({
                "module_id": val.get("module_id", ""),
                "event_id": val.get("event_id", ""),
                "attributes": attrs,
                "phase": val.get("phase", ""),
            })
        return {
            "number": block_num,
            "hash": block_hash,
            "parent_hash": header.get("parentHash", ""),
            "state_root": header.get("stateRoot", ""),
            "extrinsics_root": header.get("extrinsicsRoot", ""),
            "timestamp": ts,
            "author": str(author) if author else None,
            "extrinsic_count": len(extrinsics),
            "event_count": len(events),
            "extrinsics": ext_list,
            "events": event_list,
        }
    except Exception as e:
        return {"error": str(e)}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/explorer/status")
def api_explorer_status():
    try:
        substrate = SubstrateInterface(url=VALIDATOR_URL)
        finalised = substrate.get_chain_finalised_head()
        header = substrate.get_block_header(finalised)
        current_block = header["header"]["number"]
        chain = substrate.chain
        name = substrate.name
        substrate.close()
        return jsonify({"chain": chain, "name": name, "finalised_block": current_block})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/explorer/blocks")
def api_explorer_blocks():
    try:
        substrate = SubstrateInterface(url=VALIDATOR_URL)
        finalised = substrate.get_chain_finalised_head()
        header = substrate.get_block_header(finalised)
        current_block = header["header"]["number"]
        _from = request.args.get("from", default=current_block, type=int)
        limit = request.args.get("limit", default=20, type=int)
        limit = min(limit, 50)
        _to = max(1, _from - limit + 1)
        blocks = []
        for bn in range(_from, _to - 1, -1):
            try:
                bd = get_block_data(substrate, bn)
                if "error" not in bd:
                    blocks.append({
                        "number": bd["number"],
                        "hash": bd["hash"],
                        "timestamp": bd["timestamp"],
                        "extrinsic_count": bd["extrinsic_count"],
                        "event_count": bd["event_count"],
                        "author": bd["author"],
                    })
            except:
                pass
        substrate.close()
        return jsonify({"blocks": blocks, "latest": current_block})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/explorer/block/<int:num>")
def api_explorer_block(num):
    try:
        substrate = SubstrateInterface(url=VALIDATOR_URL)
        bd = get_block_data(substrate, num)
        substrate.close()
        if "error" in bd:
            return jsonify(bd), 404
        return jsonify(bd)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/explorer/search")
def api_explorer_search():
    q = request.args.get("q", "").strip()
    if not q:
        return jsonify({"error": "No query"}), 400
    if q.isdigit():
        return jsonify({"type": "block", "value": int(q)})
    if q.startswith("0x"):
        try:
            substrate = SubstrateInterface(url=VALIDATOR_URL)
            block_num = substrate.get_block_number(q)
            substrate.close()
            if block_num:
                return jsonify({"type": "block", "value": block_num, "hash": q})
        except:
            pass
        return jsonify({"type": "hash", "value": q})
    return jsonify({"type": "address", "value": q})

@app.route("/api/wallet/<address>")
def api_wallet(address):
    global _running_scans
    do_scan = request.args.get("scan", "").lower() in ("true", "1", "yes")
    try:
        substrate = SubstrateInterface(url=VALIDATOR_URL)
        finalized = substrate.get_chain_finalised_head()
        header = substrate.get_block_header(finalized)
        current_block = header["header"]["number"]
        result = substrate.query("System", "Account", [address])
        if result is None or result.value is None:
            substrate.close()
            return jsonify({"error": "Account not found", "block": current_block}), 404
        data = result.value
        nonce = data["nonce"]
        free = int(data["data"]["free"])
        reserved = int(data["data"]["reserved"])
        substrate.close()

        cache = load_cache(address)

        if do_scan:
            need_scan = False
            if not cache or not cache.get("status"):
                need_scan = True
                cache = {"status": "starting", "progress": 0, "scanned": 0, "total": current_block, "events": [], "transactions": [], "last_scanned": 0}
            elif cache.get("status") == "complete" and cache.get("last_scanned", 0) < current_block:
                need_scan = True
                cache["status"] = "resuming"
                save_cache(address, cache)
            elif cache.get("status") in ("scanning", "resuming", "starting"):
                if address not in _running_scans:
                    need_scan = True
            else:
                pass

            if need_scan and address not in _running_scans:
                _running_scans.add(address)
                t = threading.Thread(target=scan_full_history, args=(address, current_block), daemon=True)
                t.start()

            cache = load_cache(address)

        return jsonify({
            "block": current_block, "address": address,
            "nonce": nonce, "free": free / 10**12, "reserved": reserved / 10**12, "total": (free + reserved) / 10**12,
            "cache": cache,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route("/api/scan-status/<address>")
def api_scan_status(address):
    return jsonify(load_cache(address))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8081))
    app.run(host="0.0.0.0", port=port)
