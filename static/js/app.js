(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  /* ---------- Toast otomatis hilang ---------- */
  $$(".ft-toast").forEach((t, i) => {
    const close = () => { t.classList.add("hide"); setTimeout(() => t.remove(), 300); };
    t.querySelector("[data-close]")?.addEventListener("click", close);
    if (!t.hasAttribute("data-sticky")) setTimeout(close, 5200 + i * 600);
  });

  /* ---------- Salin teks ---------- */
  $$("[data-copy]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(btn.getAttribute("data-copy"));
        const old = btn.innerHTML;
        btn.innerHTML = '<i class="bi bi-check2"></i> Tersalin';
        setTimeout(() => (btn.innerHTML = old), 1600);
      } catch (e) { window.prompt("Salin tautan ini:", btn.getAttribute("data-copy")); }
    })
  );

  /* ---------- Konfirmasi lembut ---------- */
  $$("form[data-confirm]").forEach((f) =>
    f.addEventListener("submit", (e) => { if (!confirm(f.getAttribute("data-confirm"))) e.preventDefault(); })
  );

  /* ---------- Picker anggota (autocomplete) ---------- */
  function avatarHtml(p, size = 34) {
    if (p.foto) return `<img src="${esc(p.foto)}" class="avatar" style="--s:${size}px" alt="">`;
    return `<span class="avatar" style="--s:${size}px;--h:${p.hue ?? 180}">${esc(p.inisial || "?")}</span>`;
  }

  // konteks isian form (nama, tahun, jk, ayah/ibu/pasangan) untuk prediksi di server
  function formContext(form, skipRole) {
    const out = new URLSearchParams();
    if (!form) return out;
    const val = (n) => form.querySelector(`[name="${n}"]`)?.value?.trim() || "";
    if (val("nama")) out.set("nama", val("nama"));
    if (val("tanggal_lahir")) out.set("tanggal", val("tanggal_lahir"));
    else if (val("tahun_lahir")) out.set("tahun", val("tahun_lahir"));
    const jk = form.querySelector('[name="jenis_kelamin"]:checked');
    if (jk) out.set("jk_diri", jk.value);
    ["ayah", "ibu", "pasangan"].forEach((k) => {
      if (k === skipRole) return;
      if (val(`${k}_id`)) out.set(`${k}_id`, val(`${k}_id`));
      if (val(`${k}_nama`)) out.set(`${k}_nama`, val(`${k}_nama`));
    });
    return out;
  }

  $$("[data-picker]").forEach((wrap) => {
    const input = $(".picker-input input", wrap);
    const hidId = $("input[data-role=id]", wrap);
    const hidNama = $("input[data-role=nama]", wrap);
    const menu = $(".picker-menu", wrap);
    const picked = $(".picked", wrap);
    const typedHint = $(".picker-typed", wrap);
    const jk = wrap.getAttribute("data-jk") || "";
    const role = wrap.getAttribute("data-role") || "";
    const allowNew = wrap.getAttribute("data-allow-new") !== "0";
    const exclude = wrap.getAttribute("data-exclude") || "";
    let timer, items = [], hl = -1;

    const initials = (q) => q.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join("").toUpperCase();
    function showTyped() {
      // teks yang diketik tanpa memilih dari daftar tetap ikut tersimpan sebagai nama baru
      const q = input.value.trim();
      if (hidNama && !hidId.value) hidNama.value = q;
      if (typedHint) {
        typedHint.classList.toggle("d-none", !q || wrap.classList.contains("has-value"));
        $("span", typedHint).textContent = `“${q}” akan dicatat sebagai data baru bila tidak dipilih dari daftar.`;
      }
    }
    function setValue(p, isNew) {
      hidId.value = isNew ? "" : p.id;
      if (hidNama) hidNama.value = isNew ? p.nama : "";
      $(".pick-av", picked).innerHTML = avatarHtml(p, 34);
      $(".nm", picked).textContent = p.nama;
      $(".badge-new", picked).classList.toggle("d-none", !isNew);
      wrap.classList.add("has-value");
      menu.classList.remove("show");
      typedHint?.classList.add("d-none");
      wrap.dispatchEvent(new CustomEvent("picked", { bubbles: true }));
    }
    function clear() {
      hidId.value = ""; if (hidNama) hidNama.value = "";
      wrap.classList.remove("has-value"); input.value = ""; input.focus();
      showTyped();
      wrap.dispatchEvent(new CustomEvent("picked", { bubbles: true }));
    }
    $(".picker-clear", wrap)?.addEventListener("click", clear);

    function render(q) {
      let html = items.map((p, i) => {
        const why = (p.reasons || []).map((r) => `<span>${esc(r)}</span>`).join("");
        const best = i === 0 && p.score >= 0.85 && role ? `<span class="best">Paling cocok</span>` : "";
        return `<button type="button" data-i="${i}" class="${i === hl ? "hl" : ""}">${avatarHtml(p)}<span class="min-w-0"><div class="fw-semibold">${esc(p.nama)}${best}</div><div class="info">${esc(p.info || "")}</div>${why ? `<div class="why">${why}</div>` : ""}</span></button>`;
      }).join("");
      if (allowNew && q) html += `<button type="button" data-new="1" class="new"><span class="avatar" style="--s:34px;--h:160"><i class="bi bi-plus-lg"></i></span><span>Tambahkan “${esc(q)}” sebagai data baru<div class="info">Belum ada di silsilah? Tidak apa-apa, kita catat sekarang.</div></span></button>`;
      if (!html) html = `<div class="p-3 small text-muted">Tidak ditemukan.</div>`;
      menu.innerHTML = html;
      menu.classList.add("show");
    }
    async function search() {
      const q = input.value.trim();
      if (!q) { menu.classList.remove("show"); return; }
      const params = role ? formContext(wrap.closest("form"), role) : new URLSearchParams();
      params.set("q", q); params.set("jk", jk); params.set("exclude", exclude);
      if (role) params.set("peran", role);
      try {
        const r = await fetch(`/api/cari?${params}`);
        items = (await r.json()).filter((p) => String(p.id) !== exclude);
      } catch (e) { items = []; }
      hl = -1; render(q);
    }
    input.addEventListener("input", () => { showTyped(); clearTimeout(timer); timer = setTimeout(search, 200); });
    input.addEventListener("keydown", (e) => {
      const q = input.value.trim();
      const total = items.length + (allowNew && q ? 1 : 0);
      if (e.key === "ArrowDown") { hl = Math.min(hl + 1, total - 1); render(q); e.preventDefault(); }
      else if (e.key === "ArrowUp") { hl = Math.max(hl - 1, 0); render(q); e.preventDefault(); }
      else if (e.key === "Enter") {
        e.preventDefault();
        if (hl >= 0 && hl < items.length) setValue(items[hl]);
        else if (allowNew && q) setValue({ nama: q, inisial: initials(q), hue: 160 }, true);
      } else if (e.key === "Escape") menu.classList.remove("show");
    });
    menu.addEventListener("mousedown", (e) => {
      const b = e.target.closest("button");
      if (!b) return;
      e.preventDefault();
      if (b.dataset.new) { const q = input.value.trim(); setValue({ nama: q, inisial: initials(q), hue: 160 }, true); }
      else setValue(items[+b.dataset.i]);
    });
    input.addEventListener("blur", () => setTimeout(() => menu.classList.remove("show"), 150));
    input.addEventListener("focus", () => {
      if (window.matchMedia("(max-width: 767px)").matches) setTimeout(() => input.scrollIntoView({ block: "start", behavior: "smooth" }), 250);
      if (input.value.trim()) search();
    });
  });

  /* ---------- Foto: pilih, lalu geser & perbesar di dalam lingkaran ---------- */
  const fotoInput = $("#fotoInput");
  if (fotoInput) {
    const drop = $("#photoDrop"), hint = $("#fotoHint"), aturBtn = $("#aturFoto");
    const modalEl = $("#cropModal"), modal = new bootstrap.Modal(modalEl);
    const stage = $("#cropStage"), img = $("#cropImg"), zoomEl = $("#cropZoom");
    const OUT = 900;                       // ukuran hasil (px), diperkecil lagi di server
    let st = null, chosen = false, lastFile = null;

    const geom = () => { const S = stage.clientWidth, inset = S * 0.08; return { S, inset, C: S - 2 * inset }; };
    function clamp() {
      const { inset, C } = geom(), w = st.w * st.k, h = st.h * st.k;
      st.x = Math.min(inset, Math.max(inset + C - w, st.x));
      st.y = Math.min(inset, Math.max(inset + C - h, st.y));
    }
    function render() {
      clamp();
      img.style.transform = `translate(${st.x}px, ${st.y}px) scale(${st.k})`;
      zoomEl.value = st.k / st.base;
    }
    function zoomTo(z, cx, cy) {             // perbesar di sekitar titik (cx, cy) pada panggung
      const { S } = geom();
      cx = cx ?? S / 2; cy = cy ?? S / 2;
      const k = st.base * Math.max(1, Math.min(4, z));
      st.x = cx - (cx - st.x) * (k / st.k); st.y = cy - (cy - st.y) * (k / st.k); st.k = k;
      render();
    }
    async function openCropper(src) {
      // gambar diputar lewat canvas agar rotasi ikut tersimpan
      const base = await new Promise((ok, err) => { const i = new Image(); i.onload = () => ok(i); i.onerror = err; i.src = src; });
      st = { src: base, rot: 0 };
      await setRotation(0);
      modal.show();
    }
    async function setRotation(rot) {
      const b = st.src, c = document.createElement("canvas");
      const swap = rot % 180 !== 0;
      c.width = swap ? b.naturalHeight : b.naturalWidth; c.height = swap ? b.naturalWidth : b.naturalHeight;
      const x = c.getContext("2d");
      x.translate(c.width / 2, c.height / 2); x.rotate(rot * Math.PI / 180);
      x.drawImage(b, -b.naturalWidth / 2, -b.naturalHeight / 2);
      st.rot = rot; st.canvas = c; st.w = c.width; st.h = c.height;
      img.src = c.toDataURL("image/jpeg", 0.92);
      await img.decode().catch(() => {});
      layout();
    }
    function layout() {
      const { inset, C } = geom();
      if (!C) return;
      st.base = Math.max(C / st.w, C / st.h); st.k = st.base;
      st.x = inset + (C - st.w * st.k) / 2; st.y = inset + (C - st.h * st.k) / 2;
      render();
    }
    modalEl.addEventListener("shown.bs.modal", () => st && layout());

    // geser (mouse/jari) & cubit dua jari
    const ptrs = new Map(); let pinch0 = null;
    stage.addEventListener("pointerdown", (e) => { stage.setPointerCapture(e.pointerId); ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY }); });
    stage.addEventListener("pointermove", (e) => {
      if (!st || !ptrs.has(e.pointerId)) return;
      const prev = ptrs.get(e.pointerId); ptrs.set(e.pointerId, { x: e.clientX, y: e.clientY });
      if (ptrs.size === 2) {
        const [a, b] = [...ptrs.values()], d = Math.hypot(a.x - b.x, a.y - b.y), r = stage.getBoundingClientRect();
        if (!pinch0) pinch0 = { d, z: st.k / st.base };
        zoomTo(pinch0.z * d / pinch0.d, (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
      } else if (ptrs.size === 1) {
        st.x += e.clientX - prev.x; st.y += e.clientY - prev.y; render();
      }
    });
    const up = (e) => { ptrs.delete(e.pointerId); if (ptrs.size < 2) pinch0 = null; };
    stage.addEventListener("pointerup", up); stage.addEventListener("pointercancel", up);
    stage.addEventListener("wheel", (e) => {
      e.preventDefault(); const r = stage.getBoundingClientRect();
      zoomTo(st.k / st.base * (1 - e.deltaY * 0.0015), e.clientX - r.left, e.clientY - r.top);
    }, { passive: false });
    zoomEl.addEventListener("input", () => zoomTo(parseFloat(zoomEl.value)));
    $("#cropRotate").addEventListener("click", () => setRotation((st.rot + 90) % 360));

    $("#cropOk").addEventListener("click", async () => {
      const { inset, C } = geom();
      const c = document.createElement("canvas"); c.width = c.height = OUT;
      const sx = (inset - st.x) / st.k, sy = (inset - st.y) / st.k, sw = C / st.k;
      c.getContext("2d").drawImage(st.canvas, sx, sy, sw, sw, 0, 0, OUT, OUT);
      const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.88));
      const dt = new DataTransfer();
      dt.items.add(new File([blob], "foto-diatur.jpg", { type: "image/jpeg" }));
      fotoInput.files = dt.files; chosen = true;
      drop.querySelector("img")?.remove();
      drop.insertAdjacentHTML("afterbegin", `<img src="${URL.createObjectURL(blob)}" alt="">`);
      if (hint) hint.textContent = `Siap disimpan · ${Math.round(blob.size / 1024)} KB`;
      const del = $("#hapusFoto"); if (del) del.checked = false;
      aturBtn.classList.remove("d-none");
      modal.hide();
    });
    modalEl.addEventListener("hidden.bs.modal", () => {
      // dibatalkan: jangan unggah foto mentah yang belum diatur
      const f = fotoInput.files[0];
      if (!chosen && f && f.name !== "foto-diatur.jpg") { fotoInput.value = ""; if (hint) hint.textContent = "Pemilihan foto dibatalkan."; }
    });

    drop.addEventListener("click", () => fotoInput.click());
    fotoInput.addEventListener("change", async () => {
      const file = fotoInput.files[0];
      if (!file || file.name === "foto-diatur.jpg") return;
      if (!file.type.startsWith("image/")) { alert("Mohon pilih file gambar (JPG/PNG)."); fotoInput.value = ""; return; }
      chosen = false; lastFile = URL.createObjectURL(file);
      try { await openCropper(lastFile); }
      catch (e) { if (hint) hint.textContent = "Foto dipilih: " + file.name; }
    });
    aturBtn?.addEventListener("click", async () => {
      const src = lastFile || aturBtn.dataset.src;   // foto asli bila baru dipilih, atau foto tersimpan
      if (!src) return fotoInput.click();
      chosen = false;
      try { await openCropper(src); } catch (e) { fotoInput.click(); }
    });
  }

  /* ---------- Tanggal lahir: tombol "hanya tahu tahunnya" ---------- */
  $$("[data-toggle-tahun]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const box = $("#tahunBox");
      box.classList.toggle("d-none");
      if (!box.classList.contains("d-none")) $("input", box).focus();
    })
  );

  /* ---------- "Jadikan juga ibu/ayah dari": hanya bila pasangan terisi ---------- */
  const ab = $("#anakBersama");
  if (ab) {
    const sp = $('[data-picker][data-role="pasangan"]');
    const sync = () => ab.classList.toggle("d-none", !(sp && sp.classList.contains("has-value")));
    sp?.addEventListener("picked", sync); sync();
  }

  /* ---------- Form pernikahan: tanggal berakhir hanya bila tidak lagi menikah ---------- */
  $$(".nikah-form").forEach((f) => {
    const st = $("[data-status]", f), end = $("[data-selesai]", f);
    if (!st || !end) return;
    const sync = () => end.classList.toggle("d-none", st.value === "menikah");
    st.addEventListener("change", sync); sync();
  });

  /* ---------- Pinch-zoom dua jari (HP/tablet) ---------- */
  function pinch(el, getZoom, setZoom) {
    let start = 0, z0 = 1;
    const dist = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);
    el.addEventListener("touchstart", (e) => { if (e.touches.length === 2) { start = dist(e.touches); z0 = getZoom(); } }, { passive: true });
    el.addEventListener("touchmove", (e) => {
      if (e.touches.length === 2 && start) { e.preventDefault(); setZoom(z0 * dist(e.touches) / start); }
    }, { passive: false });
    el.addEventListener("touchend", (e) => { if (e.touches.length < 2) start = 0; });
  }

  /* ---------- Peta jalur: garis penyambung antar orang ---------- */
  function drawPathmap(map) {
    const svg = $("svg", map);
    const cells = $$(".pm-cell", map);
    const box = map.getBoundingClientRect();
    const z = parseFloat(map.dataset.zoom || "1");   // koordinat layar -> koordinat peta (sebelum di-scale)
    const NS = "http://www.w3.org/2000/svg";
    svg.innerHTML = "";
    const pos = (el) => {
      const r = $(".lin-node", el).getBoundingClientRect();
      const l = (r.left - box.left) / z, t = (r.top - box.top) / z, w = r.width / z, h = r.height / z;
      return { l, r: l + w, t, b: t + h, cx: l + w / 2, cy: t + h / 2 };
    };
    for (let i = 1; i < cells.length; i++) {
      const a = pos(cells[i - 1]), b = pos(cells[i]), c = cells[i];
      let d, mx, my;
      if (Math.abs(a.cy - b.cy) < 4) {                // segenerasi: garis datar
        d = `M${a.r},${a.cy} L${b.l},${b.cy}`; mx = (a.r + b.l) / 2; my = a.cy - 15;
      } else {                                         // beda generasi: lengkung dari bawah/atas kartu
        const down = b.cy > a.cy;
        const y1 = down ? a.b : a.t, y2 = down ? b.t : b.b;
        const x1 = a.cx, x2 = b.cx, ym = (y1 + y2) / 2;
        d = `M${x1},${y1} C${x1},${ym} ${x2},${ym} ${x2},${y2}`; mx = (x1 + x2) / 2; my = ym;
      }
      const path = document.createElementNS(NS, "path");
      path.setAttribute("d", d);
      if (c.dataset.marry === "1") path.classList.add(c.dataset.ex === "1" ? "ex" : "marry");
      svg.appendChild(path);
      const word = (c.dataset.marry === "1" ? (c.dataset.ex === "1" ? "💔 " : "❤ ") : "") + (c.dataset.word || "").toLowerCase();
      if (word.trim()) {
        const g = document.createElementNS(NS, "g"); g.setAttribute("class", "lbl" + (c.dataset.marry === "1" ? " marry" : ""));
        const t = document.createElementNS(NS, "text"); t.textContent = word; t.setAttribute("x", mx); t.setAttribute("y", my + 4); t.setAttribute("text-anchor", "middle");
        const rect = document.createElementNS(NS, "rect");
        g.appendChild(rect); g.appendChild(t); svg.appendChild(g);
        const tb = t.getBBox();
        rect.setAttribute("x", tb.x - 7); rect.setAttribute("y", tb.y - 3); rect.setAttribute("width", tb.width + 14); rect.setAttribute("height", tb.height + 6); rect.setAttribute("rx", 9);
      }
    }
  }
  // zoom & geser peta jalur
  $$("[data-pathmap-box]").forEach((boxEl) => {
    const map = $("[data-pathmap]", boxEl), wrap = $(".pathmap-wrap", boxEl), holder = $(".pathmap-scale", boxEl);
    const lbl = $('[data-pm="lbl"]', boxEl);
    let z = 1, userZoomed = false;
    const apply = () => {
      map.dataset.zoom = z;
      map.style.transform = `scale(${z})`;
      holder.style.width = map.offsetWidth * z + "px";
      holder.style.height = map.offsetHeight * z + "px";
      lbl.textContent = Math.round(z * 100) + "%";
      drawPathmap(map);
    };
    const setZoom = (v) => { z = Math.max(0.4, Math.min(1.6, v)); apply(); };
    const fit = () => setZoom(Math.min(1, (wrap.clientWidth - 8) / map.offsetWidth));
    $('[data-pm="in"]', boxEl).onclick = () => { userZoomed = true; setZoom(z + 0.1); };
    $('[data-pm="out"]', boxEl).onclick = () => { userZoomed = true; setZoom(z - 0.1); };
    $('[data-pm="fit"]', boxEl).onclick = () => { userZoomed = false; fit(); };
    wrap.addEventListener("wheel", (e) => {
      if (e.ctrlKey || e.metaKey) { e.preventDefault(); userZoomed = true; setZoom(z - e.deltaY * 0.002); }
    }, { passive: false });
    pinch(wrap, () => z, (v) => { userZoomed = true; setZoom(v); });
    let down = false, sx, sy, sl, st;
    wrap.addEventListener("pointerdown", (e) => { if (e.pointerType !== "mouse") return; down = true; sx = e.clientX; sy = e.clientY; sl = wrap.scrollLeft; st = wrap.scrollTop; wrap.classList.add("dragging"); });
    window.addEventListener("pointermove", (e) => { if (down) { wrap.scrollLeft = sl - (e.clientX - sx); wrap.scrollTop = st - (e.clientY - sy); } });
    window.addEventListener("pointerup", () => { down = false; wrap.classList.remove("dragging"); });
    const init = () => (userZoomed ? apply() : fit());
    if (document.fonts && document.fonts.ready) document.fonts.ready.then(init); else init();
    window.addEventListener("load", init);
    window.addEventListener("resize", init);
  });

  /* ---------- "Tambahkan anak": simpan dulu isian pernikahan yang belum tersimpan ---------- */
  $$("[data-tambah-anak]").forEach((btn) =>
    btn.addEventListener("click", (e) => {
      const f = $("#formNikahBaru");
      if (!f) return;
      const id = $('[name="pasangan_id"]', f)?.value, nm = $('[name="pasangan_nama"]', f)?.value?.trim();
      if (!id && !nm) return;            // tidak ada isian baru: lanjut seperti biasa
      e.preventDefault();
      $('[name="lanjut"]', f).value = "anak";
      f.requestSubmit ? f.requestSubmit() : f.submit();
    })
  );

  /* ---------- Cerita keluarga: tab garis ayah / ibu / pasangan ---------- */
  $$("[data-ktab]").forEach((tab) =>
    tab.addEventListener("click", () => {
      const i = tab.dataset.ktab;
      $$("[data-ktab]").forEach((t) => t.classList.toggle("active", t === tab));
      $$("[data-kpanel]").forEach((p) => p.classList.toggle("d-none", p.dataset.kpanel !== i));
    })
  );
  $$(".k-branch-story").forEach((el) =>
    el.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); location.href = el.dataset.href; })
  );

  /* ---------- Form cerita: teks contoh & judul mengikuti yang sudah diisi ---------- */
  const fc = $("#formCerita");
  if (fc) {
    const judul = $('[name="judul"]', fc), asal = $('[name="asal"]', fc);
    let nama = fc.dataset.leluhur && fc.dataset.leluhur !== "nama leluhur" ? fc.dataset.leluhur : "";
    let judulOtomatis = judul && (!judul.value || judul.value.startsWith("Keluarga Besar "));
    const render = () => {
      const a = asal?.value.trim();
      $$("[data-ph]", fc).forEach((el) => {
        el.placeholder = el.dataset.ph.replaceAll("{nama}", nama || "nama leluhur").replaceAll("{asal}", a || (el.name === "tonggak" ? "…" : "kampung halamannya"));
      });
      if (judulOtomatis && nama) judul.value = "Keluarga Besar " + nama;
    };
    judul?.addEventListener("input", () => (judulOtomatis = false));
    asal?.addEventListener("input", render);
    $$('input[name="leluhur_id"][type="radio"]', fc).forEach((r) =>
      r.addEventListener("change", () => {
        nama = r.dataset.nama;
        const lain = $('[data-picker] .picker-clear', fc);   // pilihan kartu menggantikan pencarian
        if (lain && $('[data-picker]', fc).classList.contains("has-value")) lain.click();
        render();
      })
    );
    fc.addEventListener("picked", (e) => {
      const has = e.target.classList.contains("has-value");
      nama = has ? ($(".picked .nm", e.target)?.textContent || "").split(",")[0].trim().split(/\s+/).slice(0, 2).join(" ") : "";
      if (has) $$('input[name="leluhur_id"][type="radio"]', fc).forEach((r) => (r.checked = false));
      render();
    });
    render();
  }

  /* ---------- Ikon 🔗 di kartu pohon: buka silsilah lain ---------- */
  let tlMenu = null;
  const tutupTl = () => { tlMenu?.remove(); tlMenu = null; };
  function bukaTautan(el) {
    let links = [];
    try { links = JSON.parse(el.dataset.links || "[]"); } catch (e) {}
    if (!links.length) return;
    if (links.length === 1) { window.open(links[0].url, "_blank", "noopener"); return; }
    tutupTl();
    tlMenu = document.createElement("div");
    tlMenu.className = "tl-menu";
    tlMenu.innerHTML = `<div class="tl-head">Tersambung ke silsilah lain</div>` + links.map((l) =>
      `<a href="${esc(l.url)}" target="_blank" rel="noopener"><i class="bi bi-link-45deg"></i> ${esc(l.label)} <i class="bi bi-box-arrow-up-right ms-auto"></i></a>`).join("");
    document.body.appendChild(tlMenu);
    const r = el.getBoundingClientRect();
    tlMenu.style.left = Math.max(8, Math.min(window.innerWidth - tlMenu.offsetWidth - 8, r.left)) + "px";
    tlMenu.style.top = (r.bottom + window.scrollY + 6) + "px";
  }
  document.addEventListener("click", (e) => {
    const el = e.target.closest(".tlink[data-links]");
    if (el) { e.preventDefault(); e.stopPropagation(); bukaTautan(el); return; }
    if (tlMenu && !e.target.closest(".tl-menu")) tutupTl();
  }, true);
  document.addEventListener("keydown", (e) => {
    const el = e.target.closest?.(".tlink[data-links]");
    if (el && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); bukaTautan(el); }
    if (e.key === "Escape") tutupTl();
  });

  /* ---------- Tabur bunga: semburan kelopak dari tombol + hujan kelopak + pesan ---------- */
  const tb = $("#taburBtn");
  if (tb) {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    let layer = null;
    const getLayer = () => {
      if (!layer) { layer = document.createElement("div"); layer.className = "tabur-layer"; document.body.appendChild(layer); }
      return layer;
    };
    function semburan() {
      const L = getLayer(), r = tb.getBoundingClientRect();
      const ox = r.left + r.width / 2, oy = r.top + r.height / 2;
      for (let i = 0; i < 36; i++) {            // memancar ke atas dari tombol, lalu jatuh
        const el = document.createElement("span");
        el.className = "petal p" + (i % 3);
        const dx = (Math.random() - 0.5) * Math.min(window.innerWidth, 700);
        el.style.left = ox + "px"; el.style.top = oy + "px";
        el.style.setProperty("--dx", dx + "px");
        el.style.setProperty("--up", -(120 + Math.random() * 220) + "px");
        el.style.setProperty("--fall", (window.innerHeight * (0.55 + Math.random() * 0.4)) + "px");
        el.style.setProperty("--rot", (Math.random() * 720 - 360) + "deg");
        el.style.setProperty("--sc", (0.7 + Math.random() * 0.8).toFixed(2));
        el.style.animationDuration = (2.6 + Math.random() * 1.6) + "s";
        el.style.animationDelay = (Math.random() * 0.25) + "s";
        L.appendChild(el);
        setTimeout(() => el.remove(), 5000);
      }
      for (let i = 0; i < 22; i++) {            // hujan kelopak lembut dari atas
        const el = document.createElement("span");
        el.className = "petal hujan p" + (i % 3);
        el.style.left = Math.random() * 100 + "vw";
        el.style.setProperty("--dx", (Math.random() * 80 - 40) + "px");
        el.style.setProperty("--rot", (Math.random() * 540) + "deg");
        el.style.setProperty("--sc", (0.6 + Math.random() * 0.7).toFixed(2));
        el.style.animationDuration = (3.5 + Math.random() * 2.5) + "s";
        el.style.animationDelay = (0.5 + Math.random() * 1.2) + "s";
        L.appendChild(el);
        setTimeout(() => el.remove(), 7500);
      }
    }
    function pesan(text) {
      const m = document.createElement("div");
      m.className = "tabur-pesan"; m.textContent = text;
      document.body.appendChild(m);
      setTimeout(() => m.remove(), 3400);
    }
    tb.addEventListener("click", async () => {
      if (!reduce) semburan();
      pesan("🌸 Bunga ditaburkan untuk " + (tb.dataset.nama || "beliau"));
      if (tb.classList.contains("sudah")) return;
      try {
        const r = await fetch(tb.dataset.url, { method: "POST", headers: { "X-CSRF-Token": tb.dataset.csrf } });
        const j = await r.json();
        const n = $("#taburN");
        n.textContent = j.jumlah;
        n.classList.remove("denyut"); void n.offsetWidth; n.classList.add("denyut");
        tb.classList.add("sudah");
        $("span", tb).textContent = "Bunga ditabur";
      } catch (e) {}
    });
  }

  /* ---------- Ucapan ulang tahun: jendela kirim ---------- */
  const ucapModalEl = $("#ucapModal");
  if (ucapModalEl) {
    const ucapModal = new bootstrap.Modal(ucapModalEl), form = $("#ucapForm", ucapModalEl);
    $$(".btn-ucap").forEach((b) =>
      b.addEventListener("click", () => {
        form.action = b.dataset.ucapUrl;
        $("#ucapJudul").textContent = "Untuk " + b.dataset.ucapNama;
        $("#ucapSub").textContent = "Ulang tahun ke-" + b.dataset.ucapKe + " · " + b.dataset.ucapTgl;
        ucapModal.show();
        setTimeout(() => $("textarea", form).focus(), 350);
      })
    );
    $$('input[name="stiker"]', form).forEach((r) => r.addEventListener("change", () => ($("#ucapEmoji").textContent = r.value)));
  }

  /* ---------- Kejutan ulang tahun: balon, konfeti, tiup lilin, kartu ucapan ---------- */
  const kej = $("#kejutan");
  if (kej) {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const warna = ["#a593f0", "#f1a7c4", "#f4b98f", "#9fd8bf", "#9cc6ee", "#f6d97a", "#e88fa8"];
    function konfeti(n) {
      if (reduce) return;
      const box = $("#konfeti");
      for (let i = 0; i < n; i++) {
        const c = document.createElement("span");
        c.className = "kf" + (i % 3 === 0 ? " bulat" : "");
        c.style.left = Math.random() * 100 + "%";
        c.style.background = warna[i % warna.length];
        c.style.setProperty("--dx", (Math.random() * 160 - 80) + "px");
        c.style.setProperty("--rot", (Math.random() * 900) + "deg");
        c.style.animationDuration = (2.4 + Math.random() * 2.2) + "s";
        c.style.animationDelay = (Math.random() * 0.6) + "s";
        box.appendChild(c);
        setTimeout(() => c.remove(), 5500);
      }
    }
    let dibaca = false;
    function buka() {
      kej.hidden = false;
      document.body.classList.add("kej-aktif");
      requestAnimationFrame(() => kej.classList.add("tampil"));
      konfeti(90);
      if (!dibaca) {
        dibaca = true;
        fetch(kej.dataset.baca, { method: "POST", headers: { "X-CSRF-Token": kej.dataset.csrf } }).catch(() => {});
      }
    }
    function tutup() {
      kej.classList.remove("tampil");
      document.body.classList.remove("kej-aktif");
      setTimeout(() => (kej.hidden = true), 400);
    }
    $$("[data-kej-buka]").forEach((b) => b.addEventListener("click", buka));
    $$("[data-kej-tutup]", kej).forEach((b) => b.addEventListener("click", tutup));
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !kej.hidden) tutup(); });
    $("#tiupLilin").addEventListener("click", () => {
      $("#kue").classList.add("padam");
      $("#tiupLilin").hidden = true;
      $("#kejHint").textContent = "Semoga semua permohonanmu terkabul 🤍";
      setTimeout(() => konfeti(160), 450);
      setTimeout(() => { const k = $("#kejKartu"); k.hidden = false; k.scrollIntoView({ behavior: "smooth", block: "nearest" }); }, 1100);
    });
    if (kej.dataset.auto === "1") setTimeout(buka, 600);
  }

  /* ---------- Rayakan ulang tahun: semburan konfeti dari tombol + balon + pesan ---------- */
  const rb = $("#rayakanBtn");
  if (rb) {
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const warna = ["#a593f0", "#f1a7c4", "#f4b98f", "#9fd8bf", "#9cc6ee", "#f6d97a", "#e88fa8", "#ffffff"];
    let layer = null;
    const getLayer = () => {
      if (!layer) { layer = document.createElement("div"); layer.className = "tabur-layer"; document.body.appendChild(layer); }
      return layer;
    };
    function sembur() {
      const L = getLayer(), r = rb.getBoundingClientRect();
      const ox = r.left + r.width / 2, oy = r.top + r.height / 2;
      for (let i = 0; i < 70; i++) {
        const el = document.createElement("span");
        const bentuk = i % 5 === 0 ? " pita" : i % 3 === 0 ? " bulat" : "";
        el.className = "petal konf" + bentuk;
        el.style.background = warna[i % warna.length];
        el.style.left = ox + "px"; el.style.top = oy + "px";
        el.style.setProperty("--dx", (Math.random() - 0.5) * Math.min(window.innerWidth, 900) + "px");
        el.style.setProperty("--up", -(140 + Math.random() * 280) + "px");
        el.style.setProperty("--fall", (window.innerHeight * (0.6 + Math.random() * 0.4)) + "px");
        el.style.setProperty("--rot", (Math.random() * 1080 - 540) + "deg");
        el.style.setProperty("--sc", (0.7 + Math.random() * 0.7).toFixed(2));
        el.style.animationDuration = (2.4 + Math.random() * 1.6) + "s";
        el.style.animationDelay = (Math.random() * 0.2) + "s";
        L.appendChild(el);
        setTimeout(() => el.remove(), 4800);
      }
      for (let i = 0; i < 7; i++) {          // balon terbang dari bawah layar
        const b = document.createElement("span");
        b.className = "balon-terbang b" + (i % 5);
        b.style.left = (8 + Math.random() * 84) + "vw";
        b.style.animationDelay = (0.2 + Math.random() * 0.9) + "s";
        b.style.animationDuration = (3.6 + Math.random() * 1.8) + "s";
        L.appendChild(b);
        setTimeout(() => b.remove(), 7000);
      }
    }
    rb.addEventListener("click", async () => {
      if (!reduce) sembur();
      const m = document.createElement("div");
      m.className = "tabur-pesan pesta";
      m.textContent = rb.dataset.sendiri === "1" ? "🎉 Selamat ulang tahun untukmu!" : "🎉 Ikut merayakan ulang tahun " + rb.dataset.nama + "!";
      document.body.appendChild(m);
      setTimeout(() => m.remove(), 3400);
      if (rb.classList.contains("sudah")) return;
      try {
        const r = await fetch(rb.dataset.url, { method: "POST", headers: { "X-CSRF-Token": rb.dataset.csrf } });
        const j = await r.json();
        const n = $("#rayakanN");
        n.textContent = j.jumlah;
        n.classList.remove("denyut"); void n.offsetWidth; n.classList.add("denyut");
        rb.classList.add("sudah");
        $("span", rb).textContent = "Ikut merayakan";
      } catch (e) {}
    });
  }

  /* ---------- Toggle WA saat status wafat ---------- */
  const wafat = $("#wafat");
  if (wafat) {
    const sync = () => {
      $$("[data-hide-wafat]").forEach((el) => el.classList.toggle("d-none", wafat.checked));
      $$("[data-show-wafat]").forEach((el) => el.classList.toggle("d-none", !wafat.checked));
    };
    $$("[data-toggle-twafat]").forEach((b) => b.addEventListener("click", () => $("#tahunWafat")?.classList.toggle("d-none")));
    wafat.addEventListener("change", sync); sync();
  }

  /* ---------- Bandingkan: kirim otomatis setelah dua orang dipilih ---------- */
  const cmp = $("#compareForm");
  if (cmp) {
    cmp.addEventListener("picked", () => {
      const a = $("input[name=a]", cmp).value, b = $("input[name=b]", cmp).value;
      $("#compareBtn").disabled = !(a && b);
    });
    $("#swapBtn")?.addEventListener("click", () => {
      const a = $("input[name=a]", cmp).value, b = $("input[name=b]", cmp).value;
      if (a && b) location.href = `?a=${b}&b=${a}`;
    });
  }

  /* ---------- Pohon: geser (drag) & zoom ---------- */
  const vp = $("#treeViewport");
  if (vp) {
    const canvas = $("#treeCanvas");
    let zoom = 1, down = false, sx, sy, sl, st, moved = false;
    const setZoom = (z) => { zoom = Math.max(0.35, Math.min(1.6, z)); canvas.style.zoom = zoom; $("#zoomLbl").textContent = Math.round(zoom * 100) + "%"; };
    $("#zoomIn").onclick = () => setZoom(zoom + 0.1);
    $("#zoomOut").onclick = () => setZoom(zoom - 0.1);
    $("#zoomFit").onclick = () => {
      canvas.style.zoom = 1;
      const z = Math.min(1, (vp.clientWidth - 20) / canvas.scrollWidth);
      setZoom(z); centerFocus();
    };
    vp.addEventListener("pointerdown", (e) => {
      if (e.pointerType !== "mouse") return;
      down = true; moved = false; sx = e.clientX; sy = e.clientY; sl = vp.scrollLeft; st = vp.scrollTop;
    });
    window.addEventListener("pointermove", (e) => {
      if (!down) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) + Math.abs(dy) > 4) { moved = true; vp.classList.add("dragging"); }
      vp.scrollLeft = sl - dx; vp.scrollTop = st - dy;
    });
    window.addEventListener("pointerup", () => { down = false; vp.classList.remove("dragging"); });
    vp.addEventListener("click", (e) => { if (moved) { e.preventDefault(); e.stopPropagation(); moved = false; } }, true);
    vp.addEventListener("wheel", (e) => { if (e.ctrlKey || e.metaKey) { e.preventDefault(); setZoom(zoom - e.deltaY * 0.002); } }, { passive: false });
    // HP: halaman dikunci, tinggi kotak pohon mengikuti layar (sampai di atas navigasi bawah)
    const hp = () => window.matchMedia("(max-width: 767px)").matches;
    function pasLayar() {
      if (!hp()) { document.body.classList.remove("pohon-aktif"); vp.style.height = ""; return; }
      document.body.classList.add("pohon-aktif");
      window.scrollTo(0, 0);
      const tinggiLayar = window.visualViewport ? window.visualViewport.height : window.innerHeight;
      const nav = $(".ft-bottomnav");
      const bawah = nav && getComputedStyle(nav).display !== "none" ? tinggiLayar - nav.getBoundingClientRect().top + 8 : 12;
      vp.style.height = Math.max(260, tinggiLayar - vp.getBoundingClientRect().top - bawah) + "px";
    }
    pasLayar();
    window.addEventListener("resize", pasLayar);
    window.addEventListener("orientationchange", () => setTimeout(pasLayar, 300));
    window.visualViewport?.addEventListener("resize", pasLayar);
    pinch(vp, () => zoom, setZoom);
    function centerFocus() {
      const f = $(".tnode.focus", canvas);
      if (!f) return;
      const r = f.getBoundingClientRect(), v = vp.getBoundingClientRect();
      vp.scrollLeft += r.left - v.left - v.width / 2 + r.width / 2;
      // gulir vertikal hanya bila yang difokuskan tidak terlihat (agar orang tua di atasnya tidak terpotong)
      if (r.top < v.top || r.bottom > v.bottom - 16) vp.scrollTop += r.top - v.top - v.height / 3;
    }
    requestAnimationFrame(() => {
      if (hp()) {   // HP: perkecil sampai muat selebar layar
        if (canvas.scrollWidth > vp.clientWidth) setZoom(Math.max(0.4, (vp.clientWidth - 12) / canvas.scrollWidth));
      } else if (canvas.scrollWidth > vp.clientWidth * 1.6) setZoom(Math.max(0.6, (vp.clientWidth - 20) / canvas.scrollWidth));
      if (canvas.dataset.muat) {   // pohon keluarga inti: muatkan seluruhnya (lebar & tinggi) dalam bingkai
        const awal = zoom;
        canvas.style.zoom = 1;   // ukur pada 100%
        const w = canvas.scrollWidth, h = canvas.scrollHeight;
        const z = Math.min(awal, (vp.clientHeight - 8) / h, w > vp.clientWidth ? (vp.clientWidth - 12) / w : 1);
        setZoom(Math.max(0.4, Math.min(awal, z)));
      }
      centerFocus();
    });
  }
})();
