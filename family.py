"""Graf silsilah keluarga & penentu hubungan kekerabatan.

Seluruh data orang dimuat ke memori (skala keluarga besar: ribuan orang masih ringan),
lalu ditelusuri dengan BFS ke atas (leluhur) untuk mencari leluhur bersama terdekat.
"""
from collections import defaultdict, deque

LELUHUR = {
    1: ("Ayah", "Ibu"),
    2: ("Kakek", "Nenek"),
    3: ("Buyut", "Buyut"),
    4: ("Canggah", "Canggah"),
    5: ("Wareng", "Wareng"),
    6: ("Udeg-udeg", "Udeg-udeg"),
    7: ("Gantung siwur", "Gantung siwur"),
}
KETURUNAN = {1: "Anak", 2: "Cucu", 3: "Cicit", 4: "Piut"}
SEPUPU = {1: "Sepupu", 2: "Sepupu dua kali", 3: "Sepupu tiga kali"}


def _g(pair, jk):
    """Pilih kata sesuai jenis kelamin; jika tidak diketahui tampilkan keduanya."""
    if jk == "L":
        return pair[0]
    if jk == "P":
        return pair[1]
    return "/".join(dict.fromkeys(pair))


def ancestor_term(n, jk=None):
    if n in LELUHUR:
        return _g(LELUHUR[n], jk)
    return f"Leluhur generasi ke-{n}"


def descendant_term(n):
    return KETURUNAN.get(n, f"Keturunan generasi ke-{n}")


def sepupu_term(n):
    return SEPUPU.get(n, f"Sepupu {n} kali")


TINGKAT = {0: "Pemilik data", 1: "Keluarga inti", 2: "Kerabat dekat", 3: "Kerabat jauh"}
STATUS_NIKAH = {"menikah": "Menikah", "cerai": "Bercerai", "wafat": "Berpisah karena wafat"}


def _compound(first, second):
    """'Paman' + 'Sepupu' -> 'Pamannya sepupu'; sebutan majemuk -> 'X dari y'."""
    second = second[0].lower() + second[1:]
    if " " not in first and "/" not in first:
        return f"{first}nya {second}"
    return f"{first} dari {second}"


class Family:
    def __init__(self, rows, marriages=()):
        self.people = {r["id"]: dict(r) for r in rows}
        self._children = defaultdict(list)
        self._spouses = defaultdict(set)
        # riwayat pernikahan tercatat: kunci frozenset({a, b})
        self._marr = {}
        for m in marriages:
            m = dict(m)
            a, b = m["orang1_id"], m["orang2_id"]
            if a in self.people and b in self.people:
                self._marr[frozenset((a, b))] = m
                self._spouses[a].add(b)
                self._spouses[b].add(a)
        for p in self.people.values():
            for key in ("ayah_id", "ibu_id"):
                if p[key] in self.people:
                    self._children[p[key]].append(p["id"])
            if p["ayah_id"] in self.people and p["ibu_id"] in self.people:
                self._spouses[p["ayah_id"]].add(p["ibu_id"])
                self._spouses[p["ibu_id"]].add(p["ayah_id"])
            if p.get("pasangan_id") in self.people:
                self._spouses[p["id"]].add(p["pasangan_id"])
                self._spouses[p["pasangan_id"]].add(p["id"])
        for kids in self._children.values():
            kids.sort(key=self._birth_key)

    # ---------- relasi dasar ----------
    def _birth_key(self, pid):
        """Urutan tertua: 'anak ke-' bila diisi, lalu tanggal/tahun lahir, lalu nama."""
        p = self.people[pid]
        ur = p.get("urutan_lahir")
        return (ur is None, ur or 0, p["tahun_lahir"] is None, p["tahun_lahir"] or 0,
                p.get("tanggal_lahir") or "9999", p["nama"].lower())

    def generation(self, pid, _seen=None):
        """Kedalaman generasi: 0 = leluhur tertinggi yang tercatat."""
        _seen = _seen or set()
        if pid in _seen:
            return 0
        _seen.add(pid)
        return max((1 + self.generation(x, _seen) for x in self.parents(pid)), default=0)

    def estimated_years(self):
        """Perkiraan tahun lahir bagi yang belum diisi, dari kerabat terdekat
        (±27 tahun dari orang tua/anak, sama dengan pasangan & saudara)."""
        if hasattr(self, "_est"):
            return self._est
        est = {pid: float(p["tahun_lahir"]) for pid, p in self.people.items() if p["tahun_lahir"]}
        for _ in range(8):
            changed = False
            for pid in self.people:
                if pid in est and self.people[pid]["tahun_lahir"]:
                    continue
                cands = [est[c] - 27 for c in self.children(pid) if c in est]
                cands += [est[x] + 27 for x in self.parents(pid) if x in est]
                cands += [est[x] for x in self.spouses(pid) if x in est]
                cands += [est[x] for x, _ in self.siblings(pid) if x in est]
                if cands:
                    val = sum(cands) / len(cands)
                    if est.get(pid) != val:
                        est[pid], changed = val, True
            if not changed:
                break
        for pid in self.people:  # tanpa jangkar sama sekali: pakai kedalaman generasi
            est.setdefault(pid, 1900 + 27 * self.generation(pid))
        self._est = est
        return est

    def age_key(self, pid):
        """Urutan tertua untuk seluruh keluarga (lintas generasi)."""
        p = self.people[pid]
        year = self.estimated_years()[pid]
        return (round(year), p.get("tanggal_lahir") or "~", p.get("urutan_lahir") or 99, p["nama"].lower())

    def is_older(self, a, b):
        """True/False bila bisa dipastikan A lebih tua dari B, None bila tidak diketahui."""
        pa, pb = self.people[a], self.people[b]
        ua, ub = pa.get("urutan_lahir"), pb.get("urutan_lahir")
        if ua and ub and ua != ub and set(self.parents(a)) & set(self.parents(b)):
            return ua < ub
        if pa.get("tanggal_lahir") and pb.get("tanggal_lahir") and pa["tanggal_lahir"] != pb["tanggal_lahir"]:
            return pa["tanggal_lahir"] < pb["tanggal_lahir"]
        if pa["tahun_lahir"] and pb["tahun_lahir"] and pa["tahun_lahir"] != pb["tahun_lahir"]:
            return pa["tahun_lahir"] < pb["tahun_lahir"]
        return None

    def get(self, pid):
        return self.people.get(pid)

    # ---------- pernikahan ----------
    def marriage(self, a, b):
        """Catatan pernikahan A–B; pasangan yang hanya dikenali dari anak bersama dianggap 'menikah'."""
        m = self._marr.get(frozenset((a, b)))
        if m:
            return m
        if b in self._spouses.get(a, ()):
            return {"id": None, "orang1_id": min(a, b), "orang2_id": max(a, b), "status": "menikah",
                    "tanggal_nikah": None, "tanggal_selesai": None}
        return None

    def marriages(self, pid):
        """Riwayat pernikahan seseorang, urut tanggal menikah: istri/suami ke-1, ke-2, …"""
        out = []
        for s in self._spouses.get(pid, ()):
            m = dict(self.marriage(pid, s))
            m["spouse"] = s
            m["children"] = [c for c in self._children.get(pid, []) if s in self.parents(c)]
            if m["status"] == "menikah" and (self.people[s]["wafat"] or self.people[pid]["wafat"]):
                m["status_label"] = "Menikah · telah berpulang"
            else:
                m["status_label"] = STATUS_NIKAH.get(m["status"], m["status"])
            out.append(m)
        out.sort(key=lambda m: (m["tanggal_nikah"] or "9999", m["id"] or 10 ** 9, self._birth_key(m["spouse"])))
        for i, m in enumerate(out, 1):
            m["ke"] = i
        return out

    def gender(self, pid):
        """Jenis kelamin tercatat, atau disimpulkan dari peran sebagai ayah/ibu dan dari pasangan."""
        p = self.people[pid]
        if p["jenis_kelamin"]:
            return p["jenis_kelamin"]
        for c in self._children.get(pid, []):
            if self.people[c]["ayah_id"] == pid:
                return "L"
            if self.people[c]["ibu_id"] == pid:
                return "P"
        for s in self._spouses.get(pid, ()):
            if self.people[s]["jenis_kelamin"] in ("L", "P"):
                return {"L": "P", "P": "L"}[self.people[s]["jenis_kelamin"]]
        return None

    def active_spouses(self, pid):
        """Pasangan yang pernikahannya masih berjalan (belum cerai, keduanya masih hidup)."""
        return [m["spouse"] for m in self.marriages(pid)
                if m["status"] == "menikah" and not self.people[pid]["wafat"] and not self.people[m["spouse"]]["wafat"]]

    def close_kin(self, a, b):
        """Orang tua/anak/leluhur/keturunan/saudara kandung — tidak mungkin menjadi pasangan."""
        r = self.blood(a, b)
        return bool(r and (r["da"] == 0 or r["db"] == 0 or (r["da"] == 1 and r["db"] == 1)))

    # ---------- jangkauan peran: Anggota / Head Family / Super Family ----------
    def scope(self, u, level):
        """Himpunan orang yang boleh dikelola pengguna (data dirinya = u) sesuai perannya."""
        if u not in self.people:
            return set()
        sp = lambda x: set(self._spouses.get(x, ()))
        ch = lambda x: set(self._children.get(x, []))
        sib = lambda x: {s for s, _ in self.siblings(x)}
        union = lambda sets: set().union(*sets) if sets else set()

        S = {u} | sp(u)
        desc = self.descendants(u)                      # anak, cucu, … beserta pasangannya
        S |= desc | union([sp(d) for d in desc])
        if level not in ("head", "super"):
            return S
        P = set(self.parents(u))                        # ayah & ibu
        G = union([set(self.parents(p)) for p in P])    # kakek & nenek kandung
        SB = sib(u)                                     # saudara kandung
        N = union([ch(s) for s in SB])                  # keponakan kandung
        AU = union([sib(p) for p in P])                 # paman & tante kandung
        C = union([ch(a) for a in AU])                  # sepupu kandung
        S |= P | G | SB | union([sp(s) for s in SB]) | N | AU | C
        if level == "super":
            S |= union([sp(n) | ch(n) for n in N])      # pasangan & anak keponakan
            S |= union([sp(c) | ch(c) for c in C])      # pasangan & anak sepupu
            S |= union([sib(g) for g in G])             # saudara kandung kakek & nenek
        return S

    # ---------- hak kelola: kedekatan kerabat ----------
    def kin_tier(self, u, p):
        """Tingkat kedekatan pengguna (data dirinya = u) terhadap orang p.
        0 pemilik · 1 keluarga inti (orang tua/leluhur, keturunan, saudara kandung, pasangan)
        · 2 kerabat dekat (paman, keponakan, sepupu, mertua, menantu, ipar) · 3 kerabat jauh."""
        if u not in self.people or p not in self.people:
            return 3
        if u == p:
            return 0
        if p in self._spouses.get(u, ()):
            return 3 if self.is_ex(u, p) else 1
        r = self.blood(u, p)
        if r:
            if r["da"] == 0 or r["db"] == 0 or (r["da"] == 1 and r["db"] == 1):
                return 1
            return 2 if r["da"] + r["db"] <= 4 else 3
        # lewat pernikahan yang masih berjalan: mertua, menantu, ipar, orang tua sambung
        for s in self.active_spouses(u):
            rs = self.blood(s, p)
            if rs and rs["da"] + rs["db"] <= 2:
                return 2
        for s in self.active_spouses(p):
            rs = self.blood(u, s)
            if rs and rs["da"] + rs["db"] <= 2:
                return 2
        return 3

    def is_ex(self, a, b):
        m = self.marriage(a, b)
        return bool(m and m["status"] == "cerai")

    def step_parents(self, pid):
        """Ayah/ibu sambung: pasangan orang tua kandung yang bukan orang tua kandungnya."""
        p, out = self.people[pid], []
        for key in ("ayah_id", "ibu_id"):
            par = p[key]
            if par not in self.people:
                continue
            for m in self.marriages(par):
                s = m["spouse"]
                if s not in self.parents(pid) and m["status"] != "cerai" and s != pid:
                    out.append({"id": s, "via": par, "marriage": m})
        return out

    def parents(self, pid):
        p = self.people.get(pid)
        if not p:
            return []
        return [x for x in (p["ayah_id"], p["ibu_id"]) if x in self.people]

    def children(self, pid):
        return list(self._children.get(pid, []))

    def spouses(self, pid):
        return sorted(self._spouses.get(pid, set()), key=self._birth_key)

    def siblings(self, pid):
        """Daftar (id, 'kandung'|'se-ayah'|'se-ibu')."""
        p = self.people.get(pid)
        if not p:
            return []
        result = {}
        for key in ("ayah_id", "ibu_id"):
            if p[key] in self.people:
                for c in self._children[p[key]]:
                    if c != pid:
                        result.setdefault(c, set()).add(key)
        out = []
        for c, keys in result.items():
            if len(keys) == 2:
                kind = "kandung"
            else:
                other = self.people[c]
                both_known = all(p[k] and other[k] for k in ("ayah_id", "ibu_id"))
                kind = ("se-ayah" if "ayah_id" in keys else "se-ibu") if both_known else "kandung"
            out.append((c, kind))
        out.sort(key=lambda t: self._birth_key(t[0]))
        return out

    def descendants(self, pid):
        seen, q = set(), deque([pid])
        while q:
            x = q.popleft()
            for c in self._children.get(x, []):
                if c not in seen:
                    seen.add(c)
                    q.append(c)
        return seen

    def generation_count(self):
        """Jumlah generasi terpanjang yang tercatat."""
        memo = {}

        def depth(pid, stack=()):
            if pid in memo:
                return memo[pid]
            if pid in stack:
                return 0
            d = 1 + max((depth(c, stack + (pid,)) for c in self._children.get(pid, [])), default=0)
            memo[pid] = d
            return d

        roots = [pid for pid in self.people if not self.parents(pid)]
        return max((depth(r) for r in roots), default=0)

    # ---------- pohon untuk tampilan ----------
    def descendant_tree(self, pid, depth, seen=None):
        seen = seen if seen is not None else {pid}
        marr = self.marriages(pid)
        node = {"id": pid, "spouses": [m["spouse"] for m in marr], "marriages": marr,
                "children": [], "more": 0, "label": None}
        kids = [c for c in self.children(pid) if c not in seen]
        if len(marr) > 1:
            # anak dikelompokkan per ibu/ayah sesuai urutan pernikahan
            order = {m["spouse"]: m["ke"] for m in marr}
            other = lambda c: next((x for x in self.parents(c) if x != pid), None)
            kids.sort(key=lambda c: (order.get(other(c), 99), self._birth_key(c)))
        if depth <= 0:
            node["more"] = len(kids)
            return node
        for c in kids:
            seen.add(c)
            child = self.descendant_tree(c, depth - 1, seen)
            if len(marr) > 1:
                o = next((x for x in self.parents(c) if x != pid), None)
                child["label"] = f"Anak dari {self.people[o]['nama'].split()[0]}" if o else None
            node["children"].append(child)
        return node

    def family_tree(self, pid, child_depth=1):
        """Pohon keluarga inti: orang tua di atas, lalu orang ini beserta saudara-saudaranya,
        dan anak-anaknya di bawah. Anak saudara diringkas ('+n anak')."""
        p = self.people[pid]
        root = next((x for x in (p["ayah_id"], p["ibu_id"]) if x in self.people), None)
        if root is None:
            return self.descendant_tree(pid, child_depth)
        node = self.descendant_tree(root, 1)
        node["label"] = {"L": "Ayah", "P": "Ibu"}.get(self.gender(root), "Orang tua")
        other = p["ibu_id"] if root == p["ayah_id"] else p["ayah_id"]
        node["spouse_label"] = {other: {"L": "Ayah", "P": "Ibu"}.get(self.gender(other), "Orang tua")} if other in self.people else {}
        kind = dict(self.siblings(pid))
        for i, c in enumerate(node["children"]):
            if c["id"] == pid:
                node["children"][i] = self.descendant_tree(pid, child_depth)
                continue
            older = self.is_older(c["id"], pid)
            sebut = "Kakak" if older else "Adik" if older is not None else "Saudara"
            k = kind.get(c["id"], "kandung")
            c["label"] = sebut if k == "kandung" else f"{sebut} {k}"
        return node

    def ancestor_tree(self, pid, depth, trail=(), with_siblings=False, with_spouses=False, child=None, level=0):
        p = self.people[pid]
        # orang tua lain dari anak di jalur ini sudah tampil sebagai kotak tersendiri
        coparent = next((x for x in self.parents(child) if x != pid), None) if child else None

        def member(x, kind):
            sp = []
            if with_spouses:
                sp = [m for m in self.marriages(x) if not (x == pid and m["spouse"] == coparent)]
            return {"id": x, "kind": kind, "spouses": sp}
        group = [(pid, None)] + (self.siblings(pid) if with_siblings else [])
        group.sort(key=lambda t: self._birth_key(t[0]))
        node = {"id": pid, "parents": [], "group": [member(x, k) for x, k in group], "level": level,
                "badge": ancestor_term(level, p["jenis_kelamin"]) if level else None}
        node["grouped"] = len(group) > 1 or any(m["spouses"] for m in node["group"])
        if depth <= 0:
            node["more"] = bool(self.parents(pid))
            return node
        for key, jk in (("ayah_id", "L"), ("ibu_id", "P")):
            par = p[key]
            if par in self.people and par not in trail:
                node["parents"].append(self.ancestor_tree(par, depth - 1, trail + (pid,), with_siblings,
                                                          with_spouses, child=pid, level=level + 1))
            else:
                node["parents"].append({"id": None, "role": key[:-3], "jk": jk, "child": pid})
        return node

    # ---------- penelusuran hubungan ----------
    def _up(self, pid):
        dist, via, q = {pid: 0}, {pid: None}, deque([pid])
        while q:
            x = q.popleft()
            for par in self.parents(x):
                if par not in dist:
                    dist[par] = dist[x] + 1
                    via[par] = x
                    q.append(par)
        return dist, via

    @staticmethod
    def _path(via, top):
        path, x = [], top
        while x is not None:
            path.append(x)
            x = via[x]
        return path[::-1]  # orang -> leluhur

    def blood(self, a, b):
        dist_a, via_a = self._up(a)
        dist_b, via_b = self._up(b)
        common = set(dist_a) & set(dist_b)
        if not common:
            return None
        best = min(dist_a[c] + dist_b[c] for c in common)
        cands = sorted((c for c in common if dist_a[c] + dist_b[c] == best),
                       key=lambda c: (abs(dist_a[c] - dist_b[c]), dist_a[c]))
        top = cands[0]
        da, db = dist_a[top], dist_b[top]
        tops = [c for c in cands if dist_a[c] == da and dist_b[c] == db]
        return {"top": top, "tops": tops, "da": da, "db": db,
                "path_a": self._path(via_a, top), "path_b": self._path(via_b, top)}

    def blood_term(self, a, b, r, jk=None):
        """(sebutan, keterangan) untuk A terhadap B berdasarkan hasil blood()."""
        pa, pb = self.people[a], self.people[b]
        jk = jk or pa["jenis_kelamin"]
        da, db = r["da"], r["db"]
        if da == 0:
            desc = "Orang tua kandung" if db == 1 else f"Garis lurus ke atas, {db} generasi"
            return ancestor_term(db, jk), desc
        if db == 0:
            desc = "Anak kandung" if da == 1 else f"Garis lurus ke bawah, {da} generasi"
            return descendant_term(da), desc
        if da == 1 and db == 1:
            shared = len(r["tops"])
            if shared >= 2 or len(self.parents(a)) < 2 or len(self.parents(b)) < 2:
                kind = "Saudara kandung"
            else:
                kind = "Saudara se-ayah" if self.people[r["top"]]["jenis_kelamin"] == "L" else "Saudara se-ibu"
            older = self.is_older(a, b)
            if older is not None:
                return ("Kakak" if older else "Adik"), kind
            return kind, "Berbagi orang tua yang sama"
        # jenis kelamin leluhur B yang menjadi titik sambung (untuk "ayah" vs "ibu")
        def via_b(level):
            return self.people[r["path_b"][level]]["jenis_kelamin"]

        if da == 1:
            label = _g(("Paman", "Bibi"), jk) if db == 2 else ancestor_term(db - 1, jk)
            return label, f"Saudara dari {ancestor_term(db - 1, via_b(db - 1)).lower()}"
        if db == 1:
            if da == 2:
                return "Keponakan", "Anak dari saudara kandung"
            return f"{descendant_term(da - 1)} keponakan", f"{descendant_term(da - 1)} dari saudara kandung"
        if da == db:
            return sepupu_term(da - 1), f"Berbagi {ancestor_term(da).lower()} yang sama"
        if da < db:
            gap = db - da
            return (f"{sepupu_term(da - 1)} dari {ancestor_term(gap, via_b(gap)).lower()}",
                    f"Satu garis sepupu, {gap} generasi di atas")
        gap = da - db
        return (f"{descendant_term(gap)} dari {sepupu_term(db - 1).lower()}",
                f"Satu garis sepupu, {gap} generasi di bawah")

    def _describe(self, a, b):
        """Hubungan A terhadap B: dict atau None."""
        r = self.blood(a, b)
        if r:
            label, desc = self.blood_term(a, b, r)
            gen = r["db"] - r["da"]
            return {"kind": "darah", "label": label, "desc": desc, "blood": r, "gen": gen,
                    "sapaan": self.sapaan(a, b, gen)}

        jk = self.people[a]["jenis_kelamin"]
        if b in self.spouses(a):
            m = self.marriage(a, b)
            if m["status"] == "cerai":
                return {"kind": "nikah", "label": "Mantan " + _g(("suami", "istri"), jk),
                        "desc": "Pernikahan telah berakhir", "chain": [a, b], "via": None}
            return {"kind": "nikah", "label": _g(("Suami", "Istri"), jk), "desc": "Pasangan hidup",
                    "chain": [a, b], "via": None}
        # besan: orang tua dari dua orang yang menikah
        for c in self.children(a):
            for s in self.spouses(c):
                if b in self.parents(s) and b != a:
                    m = self.marriage(c, s)
                    ex = bool(m and m["status"] == "cerai")
                    cucu = [k for k in self.children(c) if s in self.parents(k)]
                    pc, ps = self.people[c]["nama"].split(",")[0], self.people[s]["nama"].split(",")[0]
                    jb = self.people[b]["jenis_kelamin"]
                    return {"kind": "nikah", "label": "Mantan besan" if ex else "Besan",
                            "desc": f"Orang tua dari {pc} & {ps} yang {'pernah ' if ex else ''}menikah",
                            "chain": [a, c, s, b], "via": c, "besan": {"anak_a": c, "anak_b": s, "cucu": cucu},
                            "sapaan": {"a_ke_b": _g(("Pak Besan, atau Mas", "Bu Besan, atau Mbak"), jb),
                                       "b_ke_a": _g(("Pak Besan, atau Mas", "Bu Besan, atau Mbak"), jk)}}
        # A menikah dengan kerabat B
        for s in self.spouses(a):
            r = self.blood(s, b)
            if not r:
                continue
            special = {(1, 0): "Menantu", (2, 0): "Cucu menantu", (1, 1): "Ipar",
                       (0, 1): _g(("Ayah", "Ibu"), jk) + " sambung"}
            base, _ = self.blood_term(s, b, r)
            label = special.get((r["da"], r["db"]))
            if not label:
                own, _ = self.blood_term(s, b, r, jk=jk or "?")
                label = own if r["da"] < r["db"] else f"Pasangan {base.lower()}"
            chain = [a] + r["path_a"] + r["path_b"][::-1][1:]
            desc = f"Melalui pernikahan dengan {self.people[s]['nama']} ({base.lower()})"
            if self.is_ex(a, s):
                if (r["da"], r["db"]) == (0, 1):
                    label = _g(("Mantan suami ibu", "Mantan istri ayah"), jk)
                else:
                    label = "Mantan " + label[0].lower() + label[1:]
                desc += " — sudah bercerai"
            return {"kind": "nikah", "label": label, "desc": desc, "chain": chain, "via": s}
        # A kerabat dari pasangan B
        for s in self.spouses(b):
            r = self.blood(a, s)
            if not r:
                continue
            special = {(0, 1): "Mertua", (0, 2): _g(("Kakek", "Nenek"), jk) + " mertua",
                       (1, 1): "Ipar", (1, 0): "Anak sambung"}
            base, _ = self.blood_term(a, s, r)
            label = special.get((r["da"], r["db"]), base)
            chain = r["path_a"] + r["path_b"][::-1][1:] + [b]
            desc = f"{base} dari pasangan ({self.people[s]['nama']})"
            if self.is_ex(b, s):
                if (r["da"], r["db"]) == (1, 0):
                    label = "Anak dari mantan " + _g(("suami", "istri"), self.people[s]["jenis_kelamin"])
                else:
                    label = "Mantan " + label[0].lower() + label[1:]
                desc += " — sudah bercerai"
            return {"kind": "nikah", "label": label, "desc": desc, "chain": chain, "via": s}
        return None

    # ---------- kerabat jauh: dua keluarga yang tersambung lewat pernikahan ----------
    def _bridge(self, a, b):
        """Cari orang 'jembatan' X yang sedarah dengan A dan dengan B (mis. anak dari pernikahan
        yang menyatukan dua keluarga), beserta pasangan yang menyambungkan kedua keluarga itu."""
        best = None
        for x in self.people:
            if x in (a, b):
                continue
            ra = self.blood(a, x)
            if not ra:
                continue
            rb = self.blood(b, x)
            if not rb:
                continue
            cost = ra["da"] + ra["db"] + rb["da"] + rb["db"]
            if best is None or cost < best[0]:
                best = (cost, x, ra, rb)
        if not best:
            return None
        _, x, ra, rb = best
        couple = None
        ups, _ = self._up(x)
        for anc in sorted(ups, key=ups.get):
            for s in self.spouses(anc):
                if s in ups and (anc == a or self.blood(a, anc)) and (s == b or self.blood(b, s)) \
                        and not self.blood(a, s):
                    couple = (anc, s, self.marriage(anc, s))
                    break
            if couple:
                break
        return x, ra, rb, couple

    def _graph_path(self, a, b):
        """Jalur terpendek lewat hubungan orang tua-anak dan pernikahan."""
        prev, q = {a: None}, deque([a])
        while q:
            x = q.popleft()
            if x == b:
                break
            for y in self.parents(x) + self.children(x) + self.spouses(x):
                if y not in prev:
                    prev[y] = x
                    q.append(y)
        if b not in prev:
            return None
        path, x = [], b
        while x is not None:
            path.append(x)
            x = prev[x]
        path.reverse()
        return self.compress(path)

    def compress(self, path):
        """Orang tua yang hanya dilewati untuk menyambung saudara kandung dihilangkan."""
        out = [path[0]]
        i = 1
        while i < len(path):
            if i + 1 < len(path) and path[i] in self.parents(out[-1]) and path[i] in self.parents(path[i + 1]):
                out.append(path[i + 1])
                i += 2
            else:
                out.append(path[i])
                i += 1
        return out

    def chain_layout(self, chain):
        """Posisi tiap orang pada peta jalur: kolom = urutan, baris = generasi (0 = paling atas)."""
        gens, g = [0], 0
        for x, y in zip(chain, chain[1:]):
            if y in self.parents(x):
                g -= 1
            elif x in self.parents(y):
                g += 1
            gens.append(g)
        top = min(gens)
        # kolom setengah-lebar: beda generasi maju 1 (bertangga), segenerasi maju 3 (ada ruang label)
        cols, c = [0], 0
        for i in range(1, len(chain)):
            c += 1 if gens[i] != gens[i - 1] else 3
            cols.append(c)
        out = []
        for i, pid in enumerate(chain):
            prev = chain[i - 1] if i else None
            out.append({"id": pid, "row": gens[i] - top, "col": cols[i],
                        "word": self.step_word(prev, pid) if prev else "",
                        "marry": bool(prev and pid in self.spouses(prev)),
                        "ex": bool(prev and self.is_ex(prev, pid))})
        return out

    def step_word(self, x, y):
        """Sebutan y terhadap x untuk satu langkah jalur."""
        jk = self.people[y]["jenis_kelamin"]
        if y in self.parents(x):
            return _g(("Ayah", "Ibu"), jk)
        if x in self.parents(y):
            return "Anak"
        if set(self.parents(x)) & set(self.parents(y)):
            return "Saudara"
        if y in self.spouses(x):
            return ("Mantan " + _g(("suami", "istri"), jk)) if self.is_ex(x, y) else _g(("Suami", "Istri"), jk)
        return ""

    def sapaan(self, a, b, gen):
        """Sapaan sehari-hari. gen > 0: A satu/lebih generasi di atas B."""
        def ke_atas(p, g):  # cara memanggil orang yang g generasi di atas
            jk = p["jenis_kelamin"]
            if g == 1:
                return _g(("Om / Paman", "Tante / Bibi"), jk)
            if g == 2:
                return _g(("Kakek / Eyang", "Nenek / Eyang"), jk)
            return "Mbah Buyut"
        pa, pb = self.people[a], self.people[b]
        if gen > 0:
            return {"b_ke_a": ke_atas(pa, gen), "a_ke_b": "Nak, atau langsung nama"}
        if gen < 0:
            return {"a_ke_b": ke_atas(pb, -gen), "b_ke_a": "Nak, atau langsung nama"}
        older = self.is_older(a, b)
        kak = lambda p: _g(("Mas / Kak", "Mbak / Kak"), p["jenis_kelamin"])
        if older is None:
            return {"a_ke_b": "Nama, atau Kak bila lebih tua", "b_ke_a": "Nama, atau Kak bila lebih tua"}
        if older:
            return {"b_ke_a": kak(pa), "a_ke_b": "Dik, atau langsung nama"}
        return {"a_ke_b": kak(pb), "b_ke_a": "Dik, atau langsung nama"}

    def _distant(self, a, b):
        jk = self.people[a]["jenis_kelamin"]
        br = self._bridge(a, b)
        if br:
            x, ra, rb, couple = br
            lab_ax, _ = self.blood_term(a, x, ra)
            lab_bx, _ = self.blood_term(b, x, rb)
            rxb = self.blood(x, b)
            lab_xb, _ = self.blood_term(x, b, rxb)
            # selisih generasi: A terhadap X, ditambah X terhadap B
            gen = (ra["db"] - ra["da"]) + (rxb["db"] - rxb["da"])
            ex = bool(couple and couple[2] and couple[2]["status"] == "cerai")
            # sebutan majemuk gaya sehari-hari: "Sepupunya keponakan", "Pamannya sepupu"
            label = _compound(lab_ax, lab_xb)
            chain_full, via_couple = None, None
            if couple:
                m1, m2, _ = couple  # m1 sedarah dengan A, m2 sedarah dengan B
                pa1 = self.blood(a, m1) if a != m1 else None
                p2b = self.blood(m2, b) if m2 != b else None
                seg_a = self.compress(pa1["path_a"] + pa1["path_b"][::-1][1:]) if pa1 else [a]
                seg_b = self.compress(p2b["path_a"] + p2b["path_b"][::-1][1:]) if p2b else [b]
                chain_full = seg_a + seg_b
                # bila A orang tua/leluhur dari yang menikah: "Mertuanya sepupu"; bila B: "Sepupunya menantu"
                if pa1 and pa1["da"] == 0 and p2b:
                    via = self._describe(a, m2)
                    if via:
                        via_couple = _compound(via["label"], self.blood_term(m2, b, p2b)[0])
                else:
                    p1b = self.blood(b, m2) if m2 != b else None
                    if p1b and p1b["da"] == 0 and pa1:
                        via = self._describe(m1, b)
                        if via:
                            via_couple = _compound(self.blood_term(a, m1, pa1)[0], via["label"])
            if via_couple:
                label = via_couple
            return {"kind": "jauh", "label": label, "lab_xb": lab_xb, "gen": gen,
                    "chain_full": chain_full, "via_couple": bool(via_couple),
                    "sapaan": self.sapaan(a, b, gen),
                    "desc": "Tidak sedarah — kedua keluarga tersambung lewat pernikahan"
                            + (" yang telah berakhir" if ex else ""),
                    "bridge": x, "lab_ax": lab_ax, "lab_bx": lab_bx, "couple": couple, "ex": ex,
                    "path_a": self.compress(ra["path_a"] + ra["path_b"][::-1][1:]),
                    "path_b": self.compress(rb["path_a"] + rb["path_b"][::-1][1:])}
        path = self._graph_path(a, b)
        if path:
            steps = [self.step_word(path[i], path[i + 1]) for i in range(len(path) - 1)]
            return {"kind": "jauh", "label": "Kerabat jauh lewat pernikahan",
                    "desc": f"Tersambung melalui {len(path) - 2} orang", "bridge": None,
                    "chain": path, "steps": steps,
                    "ex": any(s.startswith("Mantan") for s in steps)}
        return None

    def relation(self, a, b):
        if a == b:
            return {"kind": "self"}
        ab = self._describe(a, b)
        if not ab:
            ab = self._distant(a, b)
            if not ab:
                return {"kind": None}
            return {"kind": "jauh", "ab": ab, "ba": dict(self._distant(b, a) or ab)}
        ba = self._describe(b, a)
        return {"kind": ab["kind"], "ab": ab, "ba": ba}
