#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
unfl_auto.py — TikTok Unfollow qua ADB (không cần tọa độ)
Luồng chạy:
  1) Hỏi/nhớ kết nối ADB (PAIR/CONNECT)
  2) Nhập username TikTok -> LẤY SỐ FOLLOWING (đang theo dõi)
  3) Hỏi bạn muốn unfollow bao nhiêu
  4) TỰ mở TikTok -> quét số + chữ “Đã follow/Following” để mở danh sách
  5) Unfollow: bấm nút xám “Đã follow/Following” -> xác nhận nút đỏ -> cuộn nhẹ
"""

import subprocess, time, re, argparse, random, json, xml.etree.ElementTree as ET
from pathlib import Path
from typing import List, Tuple, Optional
import requests

UIDUMP_REMOTE = "/sdcard/uidump.xml"
UIDUMP_LOCAL  = "uidump.xml"
CONFIG_PATH   = Path.home() / ".unfl_auto_config.json"

# ========== LOG ==========
G="\033[1;32m"; R="\033[1;31m"; Y="\033[1;33m"; C="\033[1;36m"; RS="\033[0m"

# 0=brief, 1=normal, 2=debug
LOG_LEVEL = 1
def set_log(level_name: str):
    global LOG_LEVEL
    LOG_LEVEL = {"brief":0, "normal":1, "debug":2}.get(level_name, 1)

def ok(m):   # tiến trình chính
    if LOG_LEVEL >= 0: print(f"{G}{m}{RS}")
def warn(m): # cảnh báo
    if LOG_LEVEL >= 1: print(f"{Y}{m}{RS}")
def err(m):  # lỗi (luôn in)
    print(f"{R}{m}{RS}")
def info(m): # chi tiết (TAP/SWIPE/EST/DEBUG)
    if LOG_LEVEL >= 2: print(f"{C}{m}{RS}")

# ========== SHELL / ADB ==========
def sh(cmd, timeout=25):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    return p.returncode, p.stdout.strip(), p.stderr.strip()

class ADB:
    def __init__(self, serial: Optional[str]=None): self.serial=serial
    def _base(self): return ["adb"] + (["-s", self.serial] if self.serial else [])
    def shell(self,*args,timeout=25): return sh(self._base()+["shell"]+list(args),timeout=timeout)
    def size(self)->Tuple[int,int]:
        _,out,_=self.shell("wm","size"); m=re.search(r'(\d+)x(\d+)',out or "")
        return (int(m.group(1)), int(m.group(2))) if m else (1080,2400)
    def tap(self,x:int,y:int,label=""):
        info(f"[TAP]{' '+label if label else ''}: ({x},{y})")
        self.shell("input","tap",str(x),str(y))
    def swipe(self,x1,y1,x2,y2,dur=350,label="SWIPE"):
        info(f"[{label}] ({x1},{y1})->({x2},{y2}) {dur}ms")
        self.shell("input","swipe",str(x1),str(y1),str(x2),str(y2),str(dur))
    def dump_ui(self)->str:
        # retry đơn giản để tránh dump rỗng
        errm=""
        for _ in range(3):
            self.shell("uiautomator","dump","-a",UIDUMP_REMOTE)
            _,out,errm = sh(self._base()+["shell","cat",UIDUMP_REMOTE])
            if out and out.lstrip().startswith("<?xml"):
                Path(UIDUMP_LOCAL).write_text(out,encoding="utf-8")
                return UIDUMP_LOCAL
            time.sleep(0.25)
        raise RuntimeError(f"Không lấy được UI dump: {errm or 'empty output'}")
    def launch(self, pkg: str):
        self.shell("monkey","-p",pkg,"-c","android.intent.category.LAUNCHER","1")

# ========== HELPERS ==========
def parse_bounds(b:str):
    m=re.match(r'\[(\d+),(\d+)\]\[(\d+),(\d+)\]', b or "")
    if not m: return None
    x1,y1,x2,y2=map(int,m.groups()); cx,cy=(x1+x2)//2,(y1+y2)//2
    return x1,y1,x2,y2,cx,cy

def normalize_vi(s:str)->str:
    src="áàảãạăắằẳẵặâấầẩẫậéèẻẽẹêếềểễệíìỉĩịóòỏõọôốồổỗộơớờởỡợúùủũụưứừửữựýỳỷỹỵđ"
    dst="aaaaaaaaaaaaaaaaaeeeeeeeeeeeiiiiiooooooooooooooouuuuuuuuuuuyyyyyd"
    s=s.lower()
    for a,b in zip(src,dst): s=s.replace(a,b)
    return s

def node_attr(n,key,default=""): 
    v=n.attrib.get(key)
    return v if v is not None else default

def is_exact(txt:str, target:str)->bool:
    if not txt: return False
    t=txt.strip()
    return t.lower()==target.lower() or normalize_vi(t)==normalize_vi(target)

def text_has_any(txt:str, kws:List[str])->bool:
    if not txt: return False
    low = txt.lower(); low_na = normalize_vi(low)
    return any(k in low for k in kws) or any(k in low_na for k in kws)

def parse_xy(s: str) -> Optional[Tuple[int,int]]:
    m = re.match(r"\s*(\d+)\s*,\s*(\d+)\s*$", s or "")
    return (int(m.group(1)), int(m.group(2))) if m else None

# ---- parse số hiển thị: 1.234 / 12,3K / 1,2Tr / 3.4M / 2 tỷ / 7B ----
COMPACT_RE = re.compile(r"""
    ^\s*
    (?P<num>\d{1,3}(?:[.,]\d{3})+|\d+(?:[.,]\d+)?)
    \s*
    (?P<suf>[kKmMbB]|tr|tri[eê]u|t[yỷ])?
    \s*$
""", re.X)

def parse_compact_number(s: str) -> Optional[int]:
    if not s: return None
    s = s.strip()
    m = COMPACT_RE.match(s)
    if not m: return None
    num = m.group("num").replace(",", ".")
    suf = (m.group("suf") or "").lower()
    try:
        val = float(num)
    except:
        digits = re.sub(r"[^\d]", "", num)
        if not digits: return None
        val = float(digits)
        if not suf: return int(val)
    mult = 1.0
    if   suf == "k": mult = 1e3
    elif suf == "m": mult = 1e6
    elif suf == "b": mult = 1e9
    elif suf in ("tr", "triệu", "trieu"): mult = 1e6
    elif suf in ("ty", "tỷ"): mult = 1e9
    return int(val * mult)

def parse_any_number(s: str) -> Optional[int]:
    s = (s or "").strip()
    val = parse_compact_number(s)
    if val is not None: return val
    cands = []
    for tok in re.findall(r"\d[\d.,]*\s*(?:k|m|b|tr|tri[eê]u|t[yỷ])?", s, flags=re.I):
        v = parse_compact_number(tok)
        if v is None:
            v2 = re.sub(r"[^\d]", "", tok)
            if v2:
                try: v = int(v2)
                except: v = None
        if v is not None: cands.append(v)
    return max(cands) if cands else None

# ========== CONFIG ==========
def load_cfg_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cfg_file(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    ok(f"[CFG] Đã lưu cấu hình: {CONFIG_PATH}")

# ========== MATCHERS ==========
FOLLOW_GRAY_OK = ["Đã follow", "Following"]
FOLLOW_BACK_RE = re.compile(r"(follow\s*lai|follow\s*back)", re.I)
CONFIRM_TXTS   = ["Đã follow","Bỏ theo dõi","Hủy theo dõi","Huỷ theo dõi","Unfollow","Remove"]

COUNT_LABELS = ["đã follow", "đang theo dõi", "dang theo doi", "following", "đã theo dõi"]

def pick_gray_buttons(xml_path:str, W:int, H:int):
    root=ET.parse(xml_path).getroot()
    top_cut=int(H*0.16); bot_cut=int(H*0.90)
    hits=[]
    for n in root.iter("node"):
        txt=(node_attr(n,"text","") or node_attr(n,"content-desc","")).strip()
        if not txt: continue
        if not any(is_exact(txt,t) for t in FOLLOW_GRAY_OK): continue
        if FOLLOW_BACK_RE.search(txt): continue
        b=parse_bounds(node_attr(n,"bounds")); 
        if not b: continue
        x1,y1,x2,y2,cx,cy=b
        if cx <= int(W*0.58): continue
        if not (top_cut < cy < bot_cut): continue
        hits.append((txt,b))
    hits.sort(key=lambda x:x[1][1])
    uniq=[]
    for t,b in hits:
        if not uniq or abs(b[1]-uniq[-1][1][1])>40:
            uniq.append((t,b))
    return uniq

def pick_red_confirm(xml_path:str, W:int, H:int):
    root=ET.parse(xml_path).getroot()
    lo,hi=int(H*0.30), int(H*0.95)
    for n in root.iter("node"):
        txt=(node_attr(n,"text","") or node_attr(n,"content-desc","")).strip()
        if not txt: continue
        if not any(is_exact(txt,t) for t in CONFIRM_TXTS): continue
        b=parse_bounds(node_attr(n,"bounds")); 
        if not b: continue
        x1,y1,x2,y2,cx,cy=b
        if not (lo <= cy <= hi): continue
        return (txt,b)
    return None

# ========== LẤY FOLLOWING THEO USERNAME ==========
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS_BASE = {
    "User-Agent": UA,
    "Accept-Language": "vi-VN,vi;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

def parse_html_json(html: str):
    pats = [
        r'<script id="SIGI_STATE"[^>]*>(.*?)</script>',
        r'<script id="__UNIVERSAL_DATA_FOR_REHYDRATION__"[^>]*>(.*?)</script>',
        r'<script id="__UNIVERSAL_DATA__"[^>]*>(.*?)</script>',
        r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>',
    ]
    for p in pats:
        m = re.search(p, html, re.S)
        if m:
            js = m.group(1)
            try:
                return json.loads(js)
            except Exception:
                i = js.find("{")
                if i != -1:
                    try: return json.loads(js[i:])
                    except: pass
    return None

def deep_find(obj, key):
    if isinstance(obj, dict):
        if key in obj: return obj[key]
        for v in obj.values():
            r = deep_find(v, key)
            if r is not None: return r
    elif isinstance(obj, list):
        for v in obj:
            r = deep_find(v, key)
            if r is not None: return r
    return None

def get_following_by_username(username: str, cookie: str = "") -> int:
    headers = dict(HEADERS_BASE)
    if cookie.strip():
        headers["Cookie"] = cookie.strip()

    urls = [
        f"https://www.tiktok.com/@{username}?lang=en",
        f"https://www.tiktok.com/@{username}",
    ]
    last_err = None
    with requests.Session() as s:
        for url in urls:
            try:
                r = s.get(url, headers=headers, timeout=20)
                if r.status_code != 200:
                    last_err = f"HTTP {r.status_code}"
                    continue
                if len(r.text) < 500:
                    last_err = "empty/short html"
                    continue

                data = parse_html_json(r.text)
                if not data:
                    last_err = "no json block"
                    continue

                following = deep_find(data, "followingCount")
                if following is None:
                    last_err = "missing followingCount"
                    continue

                return int(following)
            except Exception as e:
                last_err = str(e)

    raise RuntimeError(f"Không lấy được following ({last_err or 'unknown error'})")

# ========== ADB CONNECT (ghi nhớ) ==========
def wait_for_device(serial:str, timeout_s:int=12)->bool:
    t0=time.time()
    while time.time()-t0 < timeout_s:
        _,out,_=sh(["adb","devices"])
        for line in out.splitlines()[1:]:
            if line.startswith(serial) and "\tdevice" in line: return True
        time.sleep(0.6)
    return False

def connect_with_target(target:str) -> bool:
    if not target: return False
    _,o2,e2 = sh(["adb","connect",target])
    print((o2 or e2).strip() or "(no output)")
    return wait_for_device(target, 12)

def pair_connect_wizard() -> Optional[str]:
    print(Y+"=== KẾT NỐI ADB WI-FI (PAIR + CONNECT) ==="+RS)
    ip = input("IP (vd 192.168.0.x): ").strip()
    pair_port = input("PORT PAIR (popup): ").strip()
    code = input("MÃ PAIR (6 số): ").strip()
    if ip and pair_port.isdigit() and code:
        _,o,e=sh(["adb","pair",f"{ip}:{pair_port}",code]); print((o+"\n"+e).strip() or "(no output)")
    conn_port = input("PORT CONNECT (màn hình chính): ").strip()
    if ip and conn_port.isdigit():
        target = f"{ip}:{conn_port}"
        if connect_with_target(target):
            return target
    return None

def load_cfg_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}

def save_cfg_file(cfg: dict):
    CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    ok(f"[CFG] Đã lưu cấu hình: {CONFIG_PATH}")

def connect_or_remember() -> str:
    _,out,_=sh(["adb","devices"])
    for line in out.splitlines()[1:]:
        if "\tdevice" in line: 
            return line.split()[0]

    cfg = load_cfg_file()
    saved = cfg.get("adb_target")
    if saved:
        ans = input(f"Tìm thấy kết nối ADB đã lưu: {saved}. Dùng lại? (y/n): ").strip().lower()
        if ans == "y":
            if connect_with_target(saved):
                ok("[ADB] Dùng lại kết nối đã lưu.")
                return saved
            else:
                warn("[ADB] Kết nối đã lưu không online. Mở wizard PAIR/CONNECT...")

    target = pair_connect_wizard()
    if not target:
        err("Không thể kết nối ADB."); exit(1)

    ans2 = input(f"Lưu kết nối ADB này ({target}) cho lần sau? (y/n): ").strip().lower()
    if ans2 == "y":
        cfg["adb_target"] = target
        save_cfg_file(cfg)
    return target

# ========== NAV: mở app, vào Hồ sơ ==========
def autodetect_tiktok_pkg(adb: ADB) -> str:
    for pkg in ["com.ss.android.ugc.trill", "com.zhiliaoapp.musically"]:
        rc, out, _ = adb.shell("pm","path",pkg)
        if "package:" in (out or ""):
            return pkg
    return "com.ss.android.ugc.trill"

def go_profile(adb: ADB, wait=1.2) -> bool:
    keys = ["Hồ sơ", "Profile"]
    for _ in range(6):
        try:
            xml = adb.dump_ui()
            root = ET.parse(xml).getroot()
            cand = []
            for n in root.iter("node"):
                txt = (node_attr(n,"text","") or node_attr(n,"content-desc","")).strip()
                if not txt: continue
                if any(is_exact(txt,k) for k in keys):
                    b = parse_bounds(node_attr(n,"bounds"))
                    if b: cand.append(b)
            if cand:
                cand.sort(key=lambda b: (b[5], b[4]))
                x1,y1,x2,y2,cx,cy = cand[-1]
                adb.tap(cx, cy, label="Hồ sơ")
                time.sleep(wait)
                return True
        except Exception:
            pass
        W,H = adb.size()
        info(f"[TAP] (fallback) Góc phải dưới: ({int(W*0.92)},{int(H*0.94)})")
        adb.tap(int(W*0.92), int(H*0.94), label="(fallback) Góc phải dưới")
        time.sleep(wait)
    return False

# ========= XÁC THỰC ĐÃ Ở TRANG DANH SÁCH FOLLOWING =========
def _has_follow_list(adb: ADB) -> bool:
    try:
        W,H = adb.size()
        xml = adb.dump_ui()
        items = pick_gray_buttons(xml, W, H)
        return bool(items)
    except:
        return False

# ========= MỞ TAB FOLLOWING — QUÉT THEO CẶP “SỐ + LABEL” (ĐÃ VÁ) =========
def open_following_tab(adb: ADB, wait=1.0) -> bool:
    """
    Mở danh sách Following bằng cách:
      1) Ước lượng vị trí cột 'Đã follow' dựa trên label 'Follower(s)'
      2) Ghép SỐ + LABEL 'Đã follow/Following' (dọc hoặc cùng hàng) → tap giữa khối
      3) Nếu có container clickable bao cả 2 → ưu tiên tap container
      4) Mỗi lần tap xong đều kiểm tra _has_follow_list để xác thực
    """
    W, H = adb.size()
    # nắn nhẹ để lộ header
    adb.swipe(W//2, int(H*0.82), W//2, int(H*0.70), 220, label="Nắn vị trí"); time.sleep(0.25)

    # dump 1 lần để lấy nodes
    try:
        xml = adb.dump_ui()
    except RuntimeError:
        return False
    root = ET.parse(xml).getroot()

    def bounds(s):
        m=re.match(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]", s or "")
        if not m: return None
        x1,y1,x2,y2=map(int,m.groups()); return (x1,y1,x2,y2,(x1+x2)//2,(y1+y2)//2)

    def contains(outer, inner):
        x1,y1,x2,y2,_,_ = outer
        a1,b1,a2,b2,_,_ = inner
        return x1 <= a1 and y1 <= b1 and x2 >= a2 and y2 >= b2

    def looks_like_number(t):
        return bool(re.match(r"^\s*[\d.,]+\s*(?:[kKmMbB]|tr|tri[eê]u|t[yỷ])?\s*$", (t or "").strip()))

    # gom node
    nodes = []
    for n in root.iter("node"):
        t  = (n.attrib.get("text","") or n.attrib.get("content-desc","")).strip()
        b  = bounds(n.attrib.get("bounds",""))
        if not b: continue
        nodes.append({
            "n": n, "txt": t, "tn": normalize_vi(t), "b": b,
            "cx": b[4], "cy": b[5],
            "click": (n.attrib.get("clickable","false") == "true")
        })

    # -------- B1: Dựa 'Follower' để ước lượng cột trái (Đã follow)
    follower_row = [it for it in nodes if it["txt"] and it["txt"].lower() in ("follower","followers")]
    if follower_row:
        b = follower_row[0]["b"]
        est_cx, est_cy = b[4]-280, b[5]   # lệch ~280px sang trái
        info(f"[EST] Dựa vào '{follower_row[0]['txt']}' → ước lượng cột trái tại ({est_cx},{est_cy})")
        for dy in (-60,-40,-20,0,20,40,60):
            for dx in (-60,-30,0,30,60):
                tx, ty = est_cx+dx, est_cy+dy
                adb.tap(tx, ty, label="grid"); time.sleep(wait*0.9)
                if _has_follow_list(adb):
                    ok(f"✅ ĐÃ MỞ danh sách follow")
                    return True

    # -------- B2: Ghép SỐ + LABEL (da follow / following / đang theo dõi)
    labels = [it for it in nodes if it["txt"] and any(k in it["tn"] for k in ["da follow","following","dang theo doi"])]
    if labels:
        labels.sort(key=lambda it: (it["cy"], it["cx"]))
        for lb in labels:
            xlb, ylb = lb["cx"], lb["cy"]
            near_nums = []
            for it in nodes:
                if it is lb: 
                    continue
                if looks_like_number(it["txt"]):
                    # số phía TRÊN cùng cột
                    if abs(it["cx"] - xlb) <= 140 and (ylb - 240) <= it["cy"] <= (ylb - 10):
                        near_nums.append(("vertical", it))
                    # số cùng HÀNG
                    if abs(it["cy"] - ylb) <= 40 and abs(it["cx"] - xlb) <= int(W*0.35):
                        near_nums.append(("row", it))
            if not near_nums:
                continue

            kind, num_it = sorted(near_nums, key=lambda q: abs(q[1]["cy"] - ylb) + abs(q[1]["cx"] - xlb))[0]

            # container clickable bao cả 2
            containers = [it for it in nodes if it["click"] and contains(it["b"], num_it["b"]) and contains(it["b"], lb["b"])]
            if containers:
                cont = min(containers, key=lambda it: (it["b"][2]-it["b"][0])*(it["b"][3]-it["b"][1]))
                cx, cy = cont["b"][4], cont["b"][5]
                info(f"[NAV] Tap container clickable tại ({cx},{cy})")
                adb.tap(cx, cy, label="Mở Following (container)")
                time.sleep(wait)
                if _has_follow_list(adb):
                    ok(f"✅ ĐÃ MỞ danh sách Following")
                    return True

            # fallback: trung điểm khối số + nhãn
            x1=min(num_it["b"][0], lb["b"][0]); y1=min(num_it["b"][1], lb["b"][1])
            x2=max(num_it["b"][2], lb["b"][2]); y2=max(num_it["b"][3], lb["b"][3])
            cx, cy = (x1+x2)//2, (y1+y2)//2
            info(f"[NAV] Tap trung điểm pair tại ({cx},{cy})")
            adb.tap(cx, cy, label=f"Mở Following (pair {kind})")
            time.sleep(wait)
            if _has_follow_list(adb):
                ok(f"✅ ĐÃ MỞ danh sách Following")
                return True

    # -------- B3: Fallback tương đối khu vực cột trái header
    est_cx=int(W*0.24); est_cy=int(H*0.55)
    info(f"[FALLBACK] Thử lưới quanh ({est_cx},{est_cy})")
    for dy in (-60,-30,0,30,60):
        for dx in (-60,-30,0,30,60):
            tx, ty = est_cx+dx, est_cy+dy
            adb.tap(tx, ty, label="fallback"); time.sleep(wait*0.9)
            if _has_follow_list(adb):
                ok(f"✅ ĐÃ MỞ danh sách Following")
                return True

    return False

# ========== UNFOLLOW 1 MÀN HÌNH ==========
def handle_one_screen(adb, per_screen:int)->int:
    W,H=adb.size()
    xml=adb.dump_ui()
    items=pick_gray_buttons(xml, W, H)
    if not items: return 0
    done=0
    for txt,b in items:
        if done>=per_screen: break
        adb.tap(b[4], b[5], label="'Đã follow' (xám)")
        time.sleep(0.30)
        confirmed=False
        for _ in range(8):
            try: xml2=adb.dump_ui()
            except RuntimeError: time.sleep(0.2); continue
            hit=pick_red_confirm(xml2, W, H)
            if hit:
                txt2,b2=hit
                adb.tap(b2[4], b2[5], label=f"Xác nhận '{txt2}'")
                time.sleep(0.20)
                confirmed=True
                break
            time.sleep(0.18)
        if confirmed: 
            done+=1
        else:
            warn("Không thấy nút đỏ xác nhận → bỏ qua.")
    return done

# ========== MAIN ==========
def main():
    ap=argparse.ArgumentParser(description="Unfollow qua ADB (hỏi ADB -> hỏi username -> mở app -> unf)")
    ap.add_argument("--max", type=int, default=20, help="Mặc định số un nếu bạn bấm Enter")
    ap.add_argument("--per-screen", type=int, default=1)
    ap.add_argument("--delay-min", type=float, default=0.9)
    ap.add_argument("--delay-max", type=float, default=1.6)
    ap.add_argument("--log", choices=["brief","normal","debug"], default="normal")
    args=ap.parse_args()
    if args.delay_max < args.delay_min: args.delay_max = args.delay_min + 0.2
    set_log(args.log)

    # 1) ADB
    serial = connect_or_remember()
    ok(f"[ADB] Dùng thiết bị: {serial}")
    adb=ADB(serial)

    # 2) Username -> lấy FOLLOWING
    username = input("Nhập username TikTok (không có @): ").strip().lstrip("@")
    following = None
    try:
        following = get_following_by_username(username)
    except Exception as e:
        warn(f"[FOLLOW] Không lấy được số following của @{username}: {e}")
        ans = input("Bạn có muốn dán Cookie tiktok.com để thử lại? (y/n): ").strip().lower()
        if ans == "y":
            cookie = input("Dán Cookie (vd: msToken=...; tt_webid_v2=...; ...): ").strip()
            try:
                following = get_following_by_username(username, cookie=cookie)
            except Exception as e2:
                warn(f"[FOLLOW] Vẫn không lấy được: {e2}")

    if following is not None:
        ok(f"👣 @{username} đang theo dõi {following:,} tài khoản")
    else:
        warn("Không đọc được số following. Bạn vẫn có thể nhập số muốn unfollow thủ công.")

    # 3) Hỏi số cần unfollow
    default_n = args.max
    prompt = f"Bạn muốn unfollow bao nhiêu? (Enter = {default_n}"
    if following is not None:
        prompt += f", tối đa gợi ý {following}"
    prompt += "): "
    raw = input(prompt).strip()
    if raw:
        try:
            n = int(re.sub(r"[^\d]","", raw))
            if n > 0: args.max = n
        except Exception:
            warn("Giá trị nhập không hợp lệ, giữ mặc định.")

    # 4) Mở TikTok → vào Hồ sơ → mở danh sách Following (quét số + chữ)
    pkg = autodetect_tiktok_pkg(adb)
    ok(f" {pkg} — mở app")
    adb.launch(pkg); time.sleep(3.5)

    ok("Đang vào 'Hồ sơ'")
    if not go_profile(adb, wait=1.2):
        warn(" Không thể tự bấm 'Hồ sơ'. Bạn hãy vào Hồ sơ thủ công.")

    ok(" Đang mở danh sách 'Đã follow' ")
    if not open_following_tab(adb, wait=1.0):
        warn(" Không mở được . Bạn có thể mở thủ công, tool sẽ tiếp tục.")

    # 5) Unfollow
    W,H=adb.size()
    total=0
    while total < args.max:
        got=handle_one_screen(adb, args.per_screen)
        if got==0:
            adb.swipe(W//2, int(H*0.80), W//2, int(H*0.65), 350, label="Scroll Down (light)")
            time.sleep(0.5)
            continue
        total+=got
        ok(f"[✓] Đã hủy: {total}/{args.max}")
        time.sleep(random.uniform(args.delay_min,args.delay_max))
    ok(f"[DONE] Hoàn tất: {total} tài khoản.")

if __name__=="__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nĐã dừng.")