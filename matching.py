"""Pencocokan nama & prediksi hubungan.

- normalize(): buang gelar/sapaan, samakan ejaan lama (oe→u, dj→j, tj→c, …) dan variasi umum
  (Muhammad/Moh./M., Abdul/Abd.), huruf dobel dirapatkan.
- name_sim(): kemiripan 0..1 berbasis token (tahan urutan, nama panggilan, singkatan).
- rank(): memberi skor kandidat untuk sebuah peran (ayah/ibu/pasangan/diri) dengan membaca
  konteks keluarga yang sudah tercatat (pasangan, anak, orang tua, usia).
"""
import re
from difflib import SequenceMatcher

GELAR = {
    "alm", "almh", "almarhum", "almarhumah", "bpk", "bapak", "pak", "bu", "ibu", "mbah", "eyang",
    "h", "hj", "haji", "hajah", "hajjah", "kh", "dr", "drs", "dra", "ir", "prof", "st", "sh", "se",
    "spd", "skom", "sag", "ssos", "si", "mm", "mt", "mpd", "msi", "amd", "mas", "mbak", "mba", "raden",
    "rr", "ny", "nn", "tn", "sdr", "kyai", "nyai", "ustadz", "ustad", "ustazah", "dg", "daeng", "teuku", "cut",
}
VARIAN = {
    "muhamad": "muhamad", "mohamad": "muhamad", "muhammad": "muhamad", "mohammad": "muhamad",
    "moh": "muhamad", "muh": "muhamad", "mhd": "muhamad", "mochamad": "muhamad", "mukhamad": "muhamad",
    "abd": "abdul", "abdul": "abdul", "nurul": "nurul", "nur": "nur",
}
EJAAN = [("oe", "u"), ("dj", "j"), ("tj", "c"), ("sj", "sy"), ("nj", "ny"), ("ch", "kh"), ("ph", "f"), ("th", "t")]


def tokens(nama):
    s = (nama or "").lower()
    s = s.split(",")[0]                       # "Budi, S.Kom" -> "budi"
    s = re.sub(r"[^a-z\s.']", " ", s)
    out = []
    for raw in s.split():
        t = raw.replace(".", "").replace("'", "")
        if not t or t in GELAR:
            continue
        for a, b in EJAAN:
            t = t.replace(a, b)
        t = re.sub(r"(.)\1+", r"\1", t)       # huruf dobel
        t = VARIAN.get(t, t)
        out.append(t)
    return out


def normalize(nama):
    return " ".join(tokens(nama))


def _r(a, b):
    return SequenceMatcher(None, a, b).ratio()


def _tok_sim(a, b):
    if a == b:
        return 1.0
    if len(a) == 1 or len(b) == 1:            # inisial: "M" vs "Muhamad"
        return 0.85 if a[0] == b[0] else 0.0
    r = _r(a, b)
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        r = max(r, 0.8)                       # panggilan: "Sum" / "Sumiati"
    return r


def name_sim(a, b):
    ta, tb = (tokens(a), tokens(b)) if isinstance(a, str) else (a, b)
    if not ta or not tb:
        return 0.0
    short, long_ = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    best = [max(_tok_sim(t, u) for u in long_) for t in short]
    avg = sum(best) / len(best)
    coverage = len(short) / len(long_)
    tok = avg * (0.78 + 0.22 * coverage)
    full = _r(" ".join(ta), " ".join(tb))
    return round(max(tok, full), 3)


# kata depan yang sangat umum: bukan pembeda nama diri
DEPAN_UMUM = {"muhamad", "siti", "nur", "abdul", "raden", "rr", "andi", "teuku", "cut", "gusti", "i", "ni",
              "made", "putu", "ketut", "kadek", "komang", "wayan", "nyoman"}


def given_token(ts):
    """Nama diri: token pertama setelah kata depan umum ('Siti Aminah' -> 'aminah')."""
    ts = list(ts)
    while len(ts) > 1 and (ts[0] in DEPAN_UMUM or len(ts[0]) == 1):   # "M. Iqbal" -> "iqbal"
        ts.pop(0)
    return ts[0] if ts else ""


def given_name_ok(a, b):
    """Nama diri harus cocok agar dua nama dianggap orang yang sama.
    'Ahmad Junaidi Auli' vs 'Abdullah Fadri Auli' -> False (hanya nama keluarga yang sama)."""
    ta, tb = (tokens(a), tokens(b)) if isinstance(a, str) else (a, b)
    if not ta or not tb:
        return False
    ga, gb = given_token(ta), given_token(tb)
    if len(ga) == 1 or len(gb) == 1:          # inisial: "M. Yusuf"
        return ga[0] == gb[0]
    if _tok_sim(ga, gb) >= 0.75:
        return True
    # nama diri bisa tertulis di posisi lain: "Wiryo Karto" vs "Karto Wiryo"
    return any(_tok_sim(ga, t) >= 0.86 for t in tb) and any(_tok_sim(gb, t) >= 0.86 for t in ta)


def quick_match(q, nama):
    """Skor untuk kotak pencarian: kemiripan + bonus bila potongan teks cocok."""
    nq, nn = normalize(q), normalize(nama)
    if not nq:
        return 0.0
    s = name_sim(nq.split(), nn.split()) if nn else 0.0
    if nq in nn:
        s = max(s, 0.72 + 0.28 * len(nq) / max(len(nn), 1))
    if q.lower() in (nama or "").lower():
        s = max(s, 0.7)
    return s


# ---------- prediksi berbasis konteks keluarga ----------
def _similar_to(fam, pid, ref):
    """ref = {'id':..,'nama':..}. True bila orang pid sama/mirip dengan ref."""
    if not ref:
        return False
    if ref.get("id") and ref["id"] == pid:
        return True
    return bool(ref.get("nama")) and name_sim(fam.people[pid]["nama"], ref["nama"]) >= 0.8


def _ref_known(ref):
    return bool(ref and (ref.get("id") or ref.get("nama")))


def score_candidate(fam, cid, role, query, ctx):
    """Skor kandidat `cid` untuk peran `role`. ctx: nama, jk, tahun, id, ayah, ibu, pasangan (dict id/nama)."""
    c = fam.people[cid]
    need = {"ayah": "L", "ibu": "P"}.get(role)
    if role == "pasangan" and ctx.get("jk") in ("L", "P"):
        need = "P" if ctx["jk"] == "L" else "L"
    if role == "diri":
        need = ctx.get("jk") if ctx.get("jk") in ("L", "P") else None
    if ctx.get("need_jk") in ("L", "P"):
        need = ctx["need_jk"]
    cjk = fam.gender(cid) if hasattr(fam, "gender") else c["jenis_kelamin"]
    if need and cjk and cjk != need:
        return None
    if role == "pasangan" and hasattr(fam, "active_spouses"):
        # masih terikat pernikahan dengan orang lain, atau kerabat dekat: bukan calon pasangan
        if [s for s in fam.active_spouses(cid) if s != ctx.get("id")]:
            return None
        if ctx.get("id") in fam.people and fam.close_kin(cid, ctx["id"]):
            return None

    base = quick_match(query, c["nama"]) if query else 0.0
    if query:
        # data keluarga hanya menguatkan, tidak menggantikan kemiripan nama:
        # saudara kandung juga berbagi ayah & ibu yang sama, tetapi namanya berbeda.
        if not given_name_ok(query, c["nama"]):
            return None
        if base < (0.78 if role == "diri" else 0.62):
            return None
    score, reasons = base, []
    if base:
        reasons.append(f"Nama mirip {round(base * 100)}%")
    th, ct = ctx.get("tahun"), c["tahun_lahir"]
    spouses = fam.spouses(cid)
    kids = fam.children(cid)

    if role in ("ayah", "ibu"):
        other = ctx.get("ibu" if role == "ayah" else "ayah")
        if _ref_known(other) and spouses:
            match = next((s for s in spouses if _similar_to(fam, s, other)), None)
            if match:
                score += 0.22
                reasons.append(f"{'Istri' if role == 'ayah' else 'Suami'}nya {fam.people[match]['nama']} — cocok")
            else:
                score -= 0.05
        if ctx.get("id") and ctx["id"] in kids:
            score += 0.3
            reasons.append("Sudah tercatat sebagai orang tua orang ini")
        elif ctx.get("nama"):
            sib = next((k for k in kids if name_sim(fam.people[k]["nama"], ctx["nama"]) >= 0.86), None)
            if sib:
                score += 0.12
                reasons.append(f"Punya anak bernama {fam.people[sib]['nama']}")
        if th and ct:
            gap = th - ct
            if 14 <= gap <= 65:
                score += 0.05
                reasons.append(f"Usia cocok ({gap} th lebih tua)")
            else:
                score -= 0.35
                reasons.append("Usia kurang cocok sebagai orang tua")
    elif role == "pasangan":
        if ctx.get("id") and ctx["id"] in spouses and base >= 0.5:
            score += 0.3
            reasons.append("Sudah tercatat sebagai pasangan")
        elif ctx.get("nama") and spouses:
            if any(name_sim(fam.people[s]["nama"], ctx["nama"]) >= 0.86 for s in spouses):
                score += 0.2
                reasons.append("Pasangannya tercatat dengan nama yang mirip")
        if th and ct and abs(th - ct) > 30:
            score -= 0.3
    elif role == "diri":
        for key, label in (("ayah", "Ayah"), ("ibu", "Ibu")):
            ref, par = ctx.get(key), c[f"{key}_id"]
            if _ref_known(ref) and par in fam.people:
                if _similar_to(fam, par, ref):
                    score += 0.18
                    reasons.append(f"{label}nya sama: {fam.people[par]['nama']}")
                else:
                    score -= 0.12
        ref = ctx.get("pasangan")
        if _ref_known(ref) and any(_similar_to(fam, s, ref) for s in spouses):
            score += 0.15
            reasons.append("Pasangannya cocok")
        tg, cg = ctx.get("tanggal"), c.get("tanggal_lahir")
        if tg and cg:
            if tg == cg:
                score += 0.15
                reasons.append("Tanggal lahir sama")
            elif tg[:4] != cg[:4]:
                score -= 0.3
                reasons.append("Tanggal lahir berbeda")
        elif th and ct:
            if abs(th - ct) <= 2:
                score += 0.06
                reasons.append("Tahun lahir sama")
            elif abs(th - ct) > 5:
                score -= 0.3
                reasons.append("Tahun lahir berbeda")
    return max(0.0, min(1.0, round(score, 3))), reasons


def describe(fam, pid):
    """Ringkasan konteks keluarga untuk ditampilkan di daftar saran."""
    p = fam.people[pid]
    bits = []
    if p.get("tanggal_lahir"):
        y, m, d = p["tanggal_lahir"].split("-")
        bits.append(f"lahir {int(d)}/{int(m)}/{y}")
    elif p["tahun_lahir"]:
        bits.append(f"lahir ±{p['tahun_lahir']}")
    ortu = [fam.people[x]["nama"] for x in fam.parents(pid)]
    if ortu:
        bits.append("anak " + " & ".join(ortu))
    sp = [fam.people[x]["nama"] for x in fam.spouses(pid)]
    if sp:
        bits.append(("istri " if p["jenis_kelamin"] == "L" else "suami " if p["jenis_kelamin"] == "P" else "pasangan ") + ", ".join(sp))
    kids = [fam.people[x]["nama"].split()[0] for x in fam.children(pid)]
    if kids:
        bits.append(f"{len(kids)} anak: " + ", ".join(kids[:3]) + ("…" if len(kids) > 3 else ""))
    return " · ".join(bits)


def rank(fam, role, query, ctx, exclude=(), limit=8, min_score=0.55):
    out = []
    for cid in fam.people:
        if cid in exclude:
            continue
        r = score_candidate(fam, cid, role, query, ctx)
        if not r:
            continue
        score, reasons = r
        if score >= min_score:
            out.append({"id": cid, "score": score, "reasons": reasons})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


def find_duplicates(fam, claimed=(), limit=50):
    """Pasangan data yang kemungkinan orang yang sama."""
    blocks = {}
    toks = {pid: tokens(p["nama"]) for pid, p in fam.people.items()}
    for pid, ts in toks.items():
        for t in ts:
            if len(t) >= 3:
                blocks.setdefault(t[:3], set()).add(pid)
    seen, out = set(), []
    for ids in blocks.values():
        ids = sorted(ids)
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                if (a, b) in seen:
                    continue
                seen.add((a, b))
                if a in claimed and b in claimed:
                    continue
                pa, pb = fam.people[a], fam.people[b]
                if pa["jenis_kelamin"] and pb["jenis_kelamin"] and pa["jenis_kelamin"] != pb["jenis_kelamin"]:
                    continue
                # orang tua-anak / saudara / pasangan jelas bukan orang yang sama
                if b in fam.parents(a) or a in fam.parents(b) or b in fam.spouses(a):
                    continue
                if set(fam.parents(a)) & set(fam.parents(b)):
                    continue
                sim = name_sim(toks[a], toks[b])
                if sim < 0.8 or not given_name_ok(toks[a], toks[b]):
                    continue
                score, reasons = sim, [f"Nama mirip {round(sim * 100)}%"]
                for key, label in (("ayah_id", "ayah"), ("ibu_id", "ibu")):
                    x, y = pa[key], pb[key]
                    if x in fam.people and y in fam.people:
                        if name_sim(toks[x], toks[y]) >= 0.8:
                            score += 0.15
                            reasons.append(f"Nama {label} mirip")
                        else:
                            score -= 0.2
                sa = [toks[s] for s in fam.spouses(a)]
                sb = [toks[s] for s in fam.spouses(b)]
                if any(name_sim(x, y) >= 0.8 for x in sa for y in sb):
                    score += 0.15
                    reasons.append("Nama pasangan mirip")
                ka = [toks[k] for k in fam.children(a)]
                kb = [toks[k] for k in fam.children(b)]
                if any(name_sim(x, y) >= 0.86 for x in ka for y in kb):
                    score += 0.12
                    reasons.append("Ada anak dengan nama mirip")
                da, db = pa.get("tanggal_lahir"), pb.get("tanggal_lahir")
                ta, tb = pa["tahun_lahir"], pb["tahun_lahir"]
                if da and db:
                    if da == db:
                        score += 0.12
                        reasons.append("Tanggal lahir sama")
                    else:
                        score -= 0.4
                        reasons.append("Tanggal lahir berbeda")
                elif ta and tb:
                    if abs(ta - tb) <= 2:
                        score += 0.05
                    elif abs(ta - tb) > 5:
                        score -= 0.4
                        reasons.append("Tahun lahir berbeda")
                if score >= 0.85:
                    out.append({"a": a, "b": b, "score": min(1.0, round(score, 3)), "reasons": reasons})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]
