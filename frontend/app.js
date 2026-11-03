"use strict";
const $ = (s) => document.querySelector(s),
  $$ = (s) => [...document.querySelectorAll(s)];
const esc = (v) =>
  String(v ?? "").replace(
    /[&<>"']/g,
    (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[
        c
      ],
  );
const roles = {
  admin: "Administrator",
  staff: "OV-Stab",
  cook: "Koch",
  user: "Benutzer",
  technical: "Technischer Benutzer",
};
let user = null,
  csrf = "",
  view = "events",
  events = [],
  templates = [],
  devices = [],
  active = null,
  formRecord = null,
  editorSubmit = null;
const kitchen = () => ["admin", "staff", "cook"].includes(user?.role),
  lead = () => ["admin", "staff"].includes(user?.role),
  admin = () => user?.role === "admin";
const btn = (label, action, cls = "subtle") =>
  `<button type="button" class="btn ${cls}" data-action="${action}">${label}</button>`;
const empty = (msg) => `<div class="empty">${esc(msg)}</div>`;
const date = (v) =>
  v
    ? new Date(v.length === 10 ? v + "T12:00:00" : v).toLocaleString(
        "de-DE",
        v.length === 10
          ? { dateStyle: "medium" }
          : {
              dateStyle: "medium",
              timeStyle: "short",
              timeZone: "Europe/Berlin",
            },
      )
    : "–";
const num = (v) =>
  Number(v).toLocaleString("de-DE", { maximumFractionDigits: 3 });
const value = (f, k) => f.get(k);
function message(text, error = false) {
  const el = $("#message");
  el.textContent = text;
  el.classList.remove("hidden");
  el.classList.toggle("critical", error);
}
function fail(e) {
  message(e.message, true);
}
async function api(path, method = "GET", body) {
  const opts = { method, credentials: "same-origin", headers: {} };
  if (method !== "GET") {
    opts.headers["X-CSRF-Token"] = csrf;
    if (body instanceof FormData) opts.body = body;
    else if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch("/api" + path, opts);
  let result = await r.json().catch(() => ({}));
  if (!r.ok) {
    if (r.status === 401 && path != "/auth/login") logoutUI();
    throw Error(
      typeof result.detail === "string"
        ? result.detail
        : Array.isArray(result.detail)
          ? result.detail
              .map((x) => `${x.loc.slice(1).join(".")}: ${x.msg}`)
              .join("\n")
          : `Anfrage fehlgeschlagen (${r.status})`,
    );
  }
  return result;
}
function bind(actions, root = $("#workspace")) {
  root.querySelectorAll("[data-action]").forEach(
    (b) =>
      (b.onclick = () =>
        Promise.resolve()
          .then(() => actions[b.dataset.action]?.(b))
          .catch(fail)),
  );
}
function table(headers, rows) {
  return `<div class="table-wrap"><table class="data"><thead><tr>${headers.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>`;
}
function field(name, label, type = "text", v = "", attrs = "") {
  return `<label>${esc(label)}<input name="${name}" type="${type}" value="${esc(v)}" ${attrs}></label>`;
}
function select(name, label, options, v) {
  return `<label>${esc(label)}<select name="${name}" aria-label="${esc(label)}">${options
    .map((o) => {
      const [id, text] = Array.isArray(o) ? o : [o, o];
      return `<option value="${esc(id)}" ${String(id) === String(v) ? "selected" : ""}>${esc(text)}</option>`;
    })
    .join("")}</select></label>`;
}
function area(name, label, v = "", attrs = "") {
  return `<label>${esc(label)}<textarea name="${name}" rows="4" ${attrs}>${esc(v)}</textarea></label>`;
}
function openEditor(title, html, submit, label = "Speichern") {
  $("#editor-title").textContent = title;
  $("#editor-fields").innerHTML = html;
  $("#editor-error").textContent = "";
  $("#editor-submit").textContent = label;
  $("#editor-submit").hidden = !submit;
  editorSubmit = submit;
  $("#editor").showModal();
}
$$(".close-editor").forEach((b) => (b.onclick = () => $("#editor").close()));
$("#editor-form").onsubmit = async (e) => {
  e.preventDefault();
  if (!editorSubmit) return;
  const button = $("#editor-submit");
  button.disabled = true;
  try {
    await editorSubmit(new FormData(e.target));
    $("#editor").close();
  } catch (err) {
    $("#editor-error").textContent = err.message;
  } finally {
    button.disabled = false;
  }
};
function logoutUI() {
  user = null;
  csrf = "";
  $("#login-screen").classList.remove("hidden");
  $("#application").classList.add("hidden");
  $("#workspace").innerHTML = "";
}
async function loadSettings() {
  const s = await api("/settings");
  $$(".ov-name").forEach((e) => (e.textContent = s.ov_name));
  document.title = s.ov_name + " · Küchenlogistik";
  $$(".brand-logo img").forEach(
    (i) => (i.src = s.logo_url + "?v=" + Date.now()),
  );
  return s;
}
function showApp() {
  $("#login-screen").classList.add("hidden");
  $("#application").classList.remove("hidden");
  $("#logged-user").textContent = user.username + " · " + roles[user.role];
  $$(".admin-only").forEach((x) => x.classList.toggle("hidden", !admin()));
  $$(".kitchen-only").forEach((x) => x.classList.toggle("hidden", !kitchen()));
}
$("#login-form").onsubmit = async (e) => {
  e.preventDefault();
  try {
    const r = await api(
      "/auth/login",
      "POST",
      Object.fromEntries(new FormData(e.target)),
    );
    user = r.user;
    csrf = r.csrf;
    e.target.reset();
    $("#login-error").textContent = "";
    showApp();
    await show("events");
  } catch (err) {
    $("#login-error").textContent = err.message;
  }
};
$("#logout").onclick = async () => {
  try {
    await api("/auth/logout", "POST");
  } catch {}
  logoutUI();
};
$$("[data-view]").forEach(
  (b) => (b.onclick = () => show(b.dataset.view).catch(fail)),
);
$("#new-event").onclick = () => editEvent().catch(fail);
async function show(name) {
  view = name;
  $("#message").classList.add("hidden");
  $$("[data-view]").forEach((b) =>
    b.classList.toggle(
      "active",
      b.dataset.view ===
        (name === "detail" ? "events" : name === "proof" ? "forms" : name),
    ),
  );
  $("#page-title").textContent = {
    events: "Einsätze & Veranstaltungen",
    detail: "Vorgang",
    stock: "Lebensmittel & Getränke",
    temperatures: "Temperaturkontrolle",
    files: "Dateiablage",
    templates: "Vorgangsvorlagen",
    forms: "Spez71-Nachweise",
    proof: "Nachweis bearbeiten",
    archive: "Archiv · 10 Jahre",
    users: "Benutzer & API",
    settings: "OV-Konfiguration",
    audit: "Änderungsprotokoll",
  }[name];
  $("#workspace").innerHTML = '<p class="muted">Daten werden geladen …</p>';
  const render = {
    events: renderEvents,
    detail: renderDetail,
    stock: renderStock,
    temperatures: renderTemperatures,
    files: () => renderFiles(),
    templates: renderTemplates,
    forms: () => renderForms(),
    proof: renderProof,
    archive: () => renderEvents(true),
    users: renderUsers,
    settings: renderSettings,
    audit: renderAudit,
  }[name];
  try {
    await render();
  } catch (e) {
    $("#workspace").innerHTML = empty(e.message);
    throw e;
  }
}
async function renderEvents(archived = false) {
  events = await api("/events" + (archived ? "?archived=true" : ""));
  $("#workspace").innerHTML =
    `${archived ? '<div class="notice">Archivierte Vorgänge sind nur für Administratoren sichtbar und schreibgeschützt. Nach zehn Jahren werden sie einschließlich Dokumenten und Nachweisen endgültig entfernt.</div>' : `<div class="intro"><div><div class="pill">VERPFLEGUNG & MATERIAL</div><h2>Alles bereit für den Einsatz?</h2><p>Einsätze, Übungen und Veranstaltungen gemeinsam vorbereiten.</p></div><div class="stats"><div><b>${events.length}</b><span>Vorgänge</span></div><div><b>${events.reduce((n, e) => n + e.item_count - e.done_count, 0)}</b><span>Offene Punkte</span></div></div></div>`}<div class="bar"><input id="event-search" placeholder="Vorgänge suchen" aria-label="Vorgänge suchen"></div><div id="event-grid" class="event-grid"></div>`;
  function draw() {
    const q = $("#event-search").value.toLowerCase();
    $("#event-grid").innerHTML =
      events
        .filter((e) =>
          (e.name + " " + e.location + " " + e.type).toLowerCase().includes(q),
        )
        .map(
          (e) =>
            `<button class="event-card" data-action="event" data-id="${e.id}"><div class="card-top"><span class="card-date">${date(e.date)}</span><span class="badge">${esc(e.type)}</span></div><h3>${esc(e.name)}</h3><p>${esc(e.location || "Kein Ort")} · ${e.people} Personen</p><p>${e.finished ? "Abgeschlossen" : "In Vorbereitung"}</p><div class="card-track"><div style="width:${e.item_count ? (e.done_count / e.item_count) * 100 : 0}%"></div></div><div class="card-foot">${e.done_count} / ${e.item_count} Punkte</div>${archived ? `<p>Endgültige Entfernung: ${date(e.retention_until)}</p>` : ""}</button>`,
        )
        .join("") || empty("Noch keine Vorgänge vorhanden.");
    bind({
      event: async (b) => {
        active = await api("/events/" + b.dataset.id);
        await show("detail");
      },
    });
  }
  $("#event-search").oninput = draw;
  draw();
}
async function editEvent(e = null) {
  templates = await api("/event-templates");
  openEditor(
    e ? "Vorgang bearbeiten" : "Vorgang anlegen",
    `<div class="form-grid">${field("name", "Bezeichnung", "text", e?.name || "", 'required maxlength="150"')}${select("type", "Vorgangstyp", ["Einsatz", "Übung", "Veranstaltung"], e?.type || "Einsatz")}${field("date", "Beginn", "date", e?.date || new Date().toLocaleDateString("en-CA"), "required")}${field("end_date", "Ende", "date", e?.end_date || "")}${field("people", "Personen", "number", e?.people || 0, 'min="0" max="100000"')}${field("owner", "Verantwortlich", "text", e?.owner || "", 'maxlength="120"')}${field("location", "Ort", "text", e?.location || "", 'maxlength="180"')}</div>${
      !e
        ? `<h3>Checklisten kombinieren</h3><div class="check-options">${templates
            .filter((t) => t.active)
            .map(
              (t) =>
                `<label><input type="checkbox" name="template_ids" value="${t.id}">${esc(t.name)}</label>`,
            )
            .join(
              "",
            )}</div><p class="muted">Inhalte und Vorlagendokumente werden als eigener Stand übernommen.</p>`
        : ""
    }`,
    async (f) => {
      const d = Object.fromEntries(f);
      d.people = Number(d.people);
      d.end_date = d.end_date || d.date;
      if (!e) d.template_ids = f.getAll("template_ids").map(Number);
      active = await api(
        "/events" + (e ? "/" + e.id : ""),
        e ? "PATCH" : "POST",
        d,
      );
      await show("detail");
    },
  );
}
async function renderDetail() {
  active = await api("/events/" + active.id);
  const e = active,
    locked = !!e.deleted_at;
  $("#workspace").innerHTML =
    `<div class="bar">${btn("← Alle Vorgänge", "back")}${kitchen() && !locked ? btn("Bearbeiten", "edit") : ""}${admin() && !locked ? btn("Ins Archiv verschieben", "archive", "danger") : ""}</div><div class="detail-title"><div><span class="pill">${esc(e.type)}</span><h2>${esc(e.name)}</h2><p>${date(e.date)} – ${date(e.end_date)} · ${esc(e.location)} · ${e.people} Personen · ${esc(e.owner)}</p></div><span class="badge">${e.done_count}/${e.item_count} erledigt</span></div>${locked ? `<div class="notice">Archiviert am ${date(e.deleted_at)}. Schreibgeschützt bis zur endgültigen Entfernung am ${date(e.retention_until)}.</div>` : ""}<div class="tabbar"><button class="tab active" data-pane="checklist">Checkliste</button><button class="tab" data-pane="documentation">Dokumentation</button><button class="tab" data-pane="files">Dokumente</button>${kitchen() ? '<button class="tab" data-pane="proofs">Spez71</button>' : ""}</div><div id="detail-pane"></div>`;
  bind({
    back: () => show(locked ? "archive" : "events"),
    edit: () => editEvent(e),
    archive: async () => {
      if (
        confirm(
          "Vorgang für zehn Jahre archivieren? Danach nur noch für Administratoren sichtbar.",
        )
      ) {
        await api("/events/" + e.id, "DELETE");
        await show("events");
      }
    },
  });
  async function pane(name) {
    $$("[data-pane]").forEach((b) =>
      b.classList.toggle("active", b.dataset.pane === name),
    );
    const target = $("#detail-pane");
    if (name === "checklist") {
      target.innerHTML =
        `<div class="bar">${kitchen() && !locked ? btn("＋ Kategorie", "category") + btn("＋ Punkt", "item") : ""}${kitchen() && !locked ? btn(e.finished ? "Wieder öffnen" : "Als abgeschlossen markieren", "finish") : ""}</div>` +
        e.categories
          .map(
            (c) =>
              `<div class="category"><div class="category-head"><h3>${esc(c)}</h3></div>${e.items
                .filter((i) => i.category === c)
                .map(
                  (i) =>
                    `<div class="check-item" data-item="${i.id}"><input class="done" type="checkbox" aria-label="${esc(i.label)} erledigt" ${i.done ? "checked" : ""} ${locked ? "disabled" : ""}><span class="item-label">${esc(i.label)}</span><div class="item-fields"><input class="quantity" aria-label="Menge für ${esc(i.label)}" value="${esc(i.quantity)}" maxlength="100" placeholder="Menge" ${locked ? "disabled" : ""}><input class="note" aria-label="Notiz für ${esc(i.label)}" value="${esc(i.note)}" maxlength="500" placeholder="Notiz" ${locked ? "disabled" : ""}>${kitchen() && !locked ? btn("Entfernen", "remove", "danger") : ""}</div></div>`,
                )
                .join("")}</div>`,
          )
          .join("");
      target.querySelectorAll("[data-item]").forEach((row) => {
        for (const [selector, key] of [
          [".done", "done"],
          [".quantity", "quantity"],
          [".note", "note"],
        ])
          row.querySelector(selector).onchange = async (ev) => {
            try {
              await api(`/events/${e.id}/items/${row.dataset.item}`, "PATCH", {
                [key]: key === "done" ? ev.target.checked : ev.target.value,
              });
            } catch (err) {
              fail(err);
              await pane("checklist");
            }
          };
      });
      bind(
        {
          category: () =>
            openEditor(
              "Kategorie hinzufügen",
              field(
                "name",
                "Kategoriename",
                "text",
                "",
                'required maxlength="150"',
              ),
              async (f) => {
                await api(`/events/${e.id}/categories`, "POST", {
                  ov_name: f.get("name"),
                });
                await renderDetail();
              },
            ),
          item: () =>
            openEditor(
              "Punkt hinzufügen",
              field(
                "category",
                "Kategorie",
                "text",
                e.categories[0] || "Weitere Punkte",
                'required list="category-names" maxlength="150"',
              ) +
                `<datalist id="category-names">${e.categories.map((c) => `<option value="${esc(c)}">`).join("")}</datalist>` +
                field(
                  "label",
                  "Bezeichnung",
                  "text",
                  "",
                  'required maxlength="200"',
                ),
              async (f) => {
                await api(
                  `/events/${e.id}/items`,
                  "POST",
                  Object.fromEntries(f),
                );
                await renderDetail();
              },
            ),
          remove: async (b) => {
            const id = b.closest("[data-item]").dataset.item;
            if (confirm("Diesen Punkt entfernen?")) {
              await api(`/events/${e.id}/items/${id}`, "DELETE");
              await renderDetail();
            }
          },
          finish: async () => {
            await api(`/events/${e.id}`, "PATCH", { finished: !e.finished });
            await renderDetail();
          },
        },
        target,
      );
    } else if (name === "documentation") {
      const fields = [
        ["planned", "Geplante Portionen"],
        ["served", "Ausgegebene Portionen"],
        ["vegetarian", "Vegetarisch / vegan"],
        ["lead", "Verantwortlich"],
        ["menu", "Speisen und Mengen"],
        ["allergens", "Allergene / Sonderkost"],
        ["hygiene", "Hygiene und Reinigung"],
        ["receipts", "Belege / Abrechnung"],
      ];
      target.innerHTML = `<div class="panel"><form id="documentation-form"><div class="form-grid">${fields.map(([k, l]) => area(k, l, e.doc[k] || "", locked || !kitchen() ? "disabled" : 'maxlength="10000"')).join("")}</div>${kitchen() && !locked ? '<button class="btn primary">Dokumentation speichern</button>' : ""}</form></div>`;
      $("#documentation-form").onsubmit = async (ev) => {
        ev.preventDefault();
        try {
          e.doc = await api(
            `/events/${e.id}/documentation`,
            "PUT",
            Object.fromEntries(new FormData(ev.target)),
          );
          message("Dokumentation gespeichert.");
        } catch (err) {
          fail(err);
        }
      };
    } else if (name === "files") await filesInto(target, e.id, null, locked);
    else await formsInto(target, e.id, locked);
  }
  $$("[data-pane]").forEach(
    (b) => (b.onclick = () => pane(b.dataset.pane).catch(fail)),
  );
  await pane("checklist");
}
async function renderTemplates() {
  templates = await api("/event-templates");
  $("#workspace").innerHTML =
    `<div class="bar"><h2>Eigene Checklisten</h2>${btn("＋ Vorlage", "new", "primary")}</div><p class="muted">Vorlagen lassen sich beim Anlegen kombinieren. Bestehende Vorgänge behalten ihren eigenen Stand.</p>${templates.map((t) => `<div class="panel"><div class="bar"><h3>${esc(t.name)} ${!t.active ? '<span class="badge">Inaktiv</span>' : ""}</h3>${btn("Bearbeiten", "edit")} ${btn("PDF-Dokumente", "files")}</div><p>${t.categories.length} Kategorien · ${t.categories.reduce((n, c) => n + c.items.length, 0)} Punkte</p><div data-template="${t.id}"></div></div>`).join("")}`;
  const panels = $$("#workspace .panel");
  panels.forEach((p, i) => (p.dataset.id = templates[i].id));
  bind({
    new: () => templateEditor(),
    edit: (b) =>
      templateEditor(
        templates.find((t) => t.id === Number(b.closest(".panel").dataset.id)),
      ),
    files: async (b) => {
      const id = Number(b.closest(".panel").dataset.id);
      await filesInto($(`[data-template="${id}"]`), null, id);
    },
  });
}
function templateEditor(t = null) {
  openEditor(
    t ? "Vorlage bearbeiten" : "Neue Vorlage",
    field(
      "name",
      "Vorlagenname",
      "text",
      t?.name || "",
      'required maxlength="150"',
    ) +
      `<label><input type="checkbox" name="active" ${t?.active !== false ? "checked" : ""}> Für neue Vorgänge verfügbar</label><div id="template-categories"></div>${btn("＋ Kategorie", "addcat")}`,
    async (f) => {
      const cats = $$("#template-categories .template-category").map((el) => ({
        name: el.querySelector(".cat-name").value,
        items: el
          .querySelector(".cat-items")
          .value.split("\n")
          .map((s) => s.trim())
          .filter(Boolean),
      }));
      await api(
        "/event-templates" + (t ? "/" + t.id : ""),
        t ? "PUT" : "POST",
        { name: f.get("name"), active: f.has("active"), categories: cats },
      );
      await show("templates");
    },
  );
  function add(c = { name: "", items: [] }) {
    const el = document.createElement("div");
    el.className = "template-category";
    el.innerHTML = `<label>Kategorie<input class="cat-name" value="${esc(c.name)}" required maxlength="150"></label><label>Punkte – einer pro Zeile<textarea class="cat-items">${esc(c.items.join("\n"))}</textarea></label><button class="btn danger" type="button">Kategorie entfernen</button>`;
    el.querySelector("button").onclick = () => el.remove();
    $("#template-categories").append(el);
  }
  (t?.categories || [{ name: "", items: [] }]).forEach(add);
  bind({ addcat: () => add() }, $("#editor-fields"));
}
async function renderStock() {
  const [articles, movements] = await Promise.all([
    api("/stock/articles"),
    api("/stock/movements"),
  ]);
  $("#workspace").innerHTML =
    `<div class="bar"><h2>Bestände</h2>${kitchen() ? btn("＋ Artikel", "new", "primary") : ""}</div>${articles.some((a) => a.negative) ? '<div class="critical alert"><strong>ACHTUNG: NEGATIVER BESTAND!</strong> Mindestens ein Bestand ist widersprüchlich. Bitte durch den OV-Stab prüfen und per Inventur berichtigen.</div>' : ""}<input id="stock-search" placeholder="Lebensmittel oder Getränke suchen" aria-label="Bestände suchen"><div id="stock-table"></div><div class="panel"><h3>Buchungsjournal</h3><p class="muted">Unveränderbare Historie. Fehler werden durch neue Buchungen ausgeglichen. Letzte 200 Buchungen.</p>${table(
      [
        "Zeit / Person",
        "Artikel",
        "Änderung",
        "Vorher → Nachher",
        "Grund / Vorgang",
      ],
      movements.map(
        (m) =>
          `<tr><td>${date(m.at)}<small>${esc(m.actor)}</small></td><td>${esc(articles.find((a) => a.id === m.article_id)?.name || m.article_id)}</td><td>${Number(m.delta) > 0 ? "+" : ""}${num(m.delta)}</td><td>${num(m.before)} → ${num(m.after)}</td><td>${esc(m.reason)}<small>${esc(m.note)}</small>${m.event_id ? `Vorgang #${m.event_id}` : ""}</td></tr>`,
      ),
    )}</div>`;
  function draw() {
    const q = $("#stock-search").value.toLowerCase();
    $("#stock-table").innerHTML = table(
      ["Artikel", "Ist", "Soll", "Status", "Aktionen"],
      articles
        .filter((a) => a.name.toLowerCase().includes(q))
        .map(
          (a) =>
            `<tr class="${a.negative ? "critical" : a.below_target ? "warning" : ""}" data-id="${a.id}"><td><strong>${esc(a.name)}</strong><small>${esc(a.kind)}${!a.active ? " · Inaktiv" : ""}</small></td><td class="number">${num(a.balance)} ${esc(a.unit)}</td><td>${num(a.target)} ${esc(a.unit)}</td><td>${a.negative ? "NEGATIVER BESTAND" : a.below_target ? "Unter Sollbestand" : "Bestand ausreichend"}</td><td>${kitchen() ? btn("Buchen", "book") + btn("Bearbeiten", "edit") : ""}</td></tr>`,
        ),
    );
    bind({
      new: () => articleEditor(),
      edit: (b) =>
        articleEditor(
          articles.find((a) => a.id === Number(b.closest("tr").dataset.id)),
        ),
      book: (b) =>
        bookEditor(
          articles.find((a) => a.id === Number(b.closest("tr").dataset.id)),
        ),
    });
  }
  $("#stock-search").oninput = draw;
  draw();
}
function articleEditor(a = null) {
  openEditor(
    a ? "Artikel bearbeiten" : "Artikel anlegen",
    field(
      "name",
      "Bezeichnung",
      "text",
      a?.name || "",
      'required maxlength="150"',
    ) +
      select("kind", "Art", ["Lebensmittel", "Getränk"], a?.kind) +
      select("unit", "Einheit", ["Stück", "Liter", "kg"], a?.unit) +
      field(
        "target",
        "Sollbestand",
        "number",
        a?.target || 0,
        'required min="0" step="0.001"',
      ) +
      `<label><input type="checkbox" name="active" ${a?.active !== false ? "checked" : ""}> Aktiv</label>`,
    async (f) => {
      const d = Object.fromEntries(f);
      d.active = f.has("active");
      await api(
        "/stock/articles" + (a ? "/" + a.id : ""),
        a ? "PUT" : "POST",
        d,
      );
      await show("stock");
    },
  );
}
async function bookEditor(a) {
  const options = await api("/events");
  const external_id = crypto.randomUUID();
  openEditor(
    a.name + " · " + num(a.balance) + " " + a.unit,
    select(
      "direction",
      "Buchungsart",
      [
        ["in", "Zugang"],
        ["out", "Entnahme"],
        ...(lead()
          ? [["inventory", "Inventur: gezählten Ist-Bestand setzen"]]
          : []),
      ],
      "out",
    ) +
      field(
        "quantity",
        "Menge / gezählter Ist-Bestand (" + a.unit + ")",
        "number",
        "",
        'required min="0" step="' + (a.unit === "Stück" ? "1" : "0.001") + '"',
      ) +
      select(
        "reason",
        "Grund",
        [
          "Verbrauch",
          "Zugang",
          "Verderb",
          "Rückgabe",
          "Inventurkorrektur",
          "Korrektur",
        ],
        "Verbrauch",
      ) +
      area("note", "Begründung", "", 'required maxlength="500"') +
      select(
        "event_id",
        "Optionaler Vorgangsbezug",
        [["", "Keiner"], ...options.map((e) => [e.id, e.name])],
        "",
      ),
    async (f) => {
      const d = Object.fromEntries(f);
      d.article_id = a.id;
      d.event_id = d.event_id ? Number(d.event_id) : null;
      d.external_id = external_id;
      await api("/stock/movements", "POST", d);
      await show("stock");
    },
  );
  const direction = $("#editor-fields [name=direction]");
  direction.onchange = () => {
    $("#editor-fields [name=reason]").value =
      direction.value === "inventory"
        ? "Inventurkorrektur"
        : direction.value === "in"
          ? "Zugang"
          : "Verbrauch";
  };
}
async function renderTemperatures() {
  devices = await api("/devices");
  $("#workspace").innerHTML =
    `<div class="bar"><h2>Kühlgeräte</h2>${kitchen() ? btn("＋ Gerät", "new", "primary") : ""}</div><div class="bar"><label class="flex-fill">Gerät<select id="device-select">${devices.map((d) => `<option value="${d.id}">${esc(d.name)} · ${esc(d.location)}${!d.active ? " (inaktiv)" : ""}</option>`).join("")}</select></label>${kitchen() ? btn("Gerät & Grenzwerte", "edit") : ""}${devices.length ? btn("＋ Temperatur eintragen", "reading", "primary") : ""}${lead() && devices.length ? btn("Zeitraum als Spez71 exportieren", "export") : ""}</div>${devices.length ? `<div class="bar">${field("start", "Von", "datetime-local", "", 'id="reading-start"')}${field("end", "Bis", "datetime-local", "", 'id="reading-end"')}${btn("Anzeigen", "range")}</div><div id="temperature-data"></div>` : empty("Noch keine Geräte angelegt.")}`;
  const selected = () =>
    devices.find((d) => d.id === Number($("#device-select").value));
  async function draw() {
    if (!selected()) return;
    const d = selected();
    let qs = `device_id=${d.id}&limit=10000`;
    for (const key of ["start", "end"]) {
      const v = $("#reading-" + key).value;
      if (v)
        qs += "&" + key + "=" + encodeURIComponent(new Date(v).toISOString());
    }
    const rows = await api("/readings?" + qs);
    $("#temperature-data").innerHTML =
      `<p class="muted">Aktueller Grenzbereich: ${num(d.lower)} bis ${num(d.upper)} °C. Markierungen verwenden die zum Messzeitpunkt gültigen Grenzen.</p>${chart(rows)}<p class="chart-caption">${rows.length} Messungen angezeigt${rows.length === 10000 ? " · Anzeige begrenzt: Zeitraum weiter eingrenzen" : ""}. Uhrzeiten: Europe/Berlin. Messwerte sind unveränderbar.</p><div class="chart-table">${table(
        ["Messzeit / Eingang", "Temperatur", "Quelle", "Bemerkungen"],
        rows
          .slice()
          .reverse()
          .map(
            (r) =>
              `<tr class="${r.outside ? "warning" : ""}" data-id="${r.id}"><td>${date(r.measured_at)}<small>Eingang: ${date(r.received_at)}</small>${r.event_id ? `<small>Vorgang #${r.event_id}</small>` : ""}</td><td class="number">${num(r.value)} °C<small>${r.outside ? "Außerhalb Grenzbereich" : "Im Grenzbereich"} · ${num(r.lower)}…${num(r.upper)} °C</small></td><td>${esc(r.actor)}<small>${r.source === "device" ? "Automatische Gerätemessung" : "Manuelle Kontrolle"}</small></td><td>${r.notes.map((n) => `${esc(n.text)}<small>${esc(n.actor)} · ${date(n.at)}</small>`).join("")}${kitchen() ? btn("Bemerkung ergänzen", "note") : ""}</td></tr>`,
          ),
      )}</div>`;
    bind(
      {
        note: (b) =>
          openEditor(
            "Bemerkung ergänzen",
            area(
              "text",
              "Bemerkung (bestehende Einträge bleiben erhalten)",
              "",
              'required maxlength="500"',
            ),
            async (f) => {
              await api(
                "/readings/" + b.closest("tr").dataset.id + "/notes",
                "POST",
                Object.fromEntries(f),
              );
              await draw();
            },
          ),
      },
      $("#temperature-data"),
    );
  }
  bind({
    new: () => deviceEditor(),
    edit: () => selected() && deviceEditor(selected()),
    reading: () => readingEditor(selected()),
    range: draw,
    export: () => exportEditor(selected()),
  });
  $("#device-select").onchange = () => draw().catch(fail);
  await draw();
}
function chart(rows) {
  if (!rows.length) return empty("Keine Messungen im ausgewählten Zeitraum.");
  const w = 900,
    h = 260,
    p = 50,
    times = rows.map((r) => new Date(r.measured_at).getTime()),
    lo = Math.min(...rows.map((r) => Number(r.value))) - 2,
    hi = Math.max(...rows.map((r) => Number(r.value))) + 2,
    start = Math.min(...times),
    end = Math.max(...times);
  const x = (i) =>
      p + ((times[i] - start) / Math.max(1, end - start)) * (w - 2 * p),
    y = (v) => h - p - ((Number(v) - lo) / (hi - lo)) * (h - 2 * p);
  let svg = `<svg class="chart" viewBox="0 0 ${w} ${h}" role="img" aria-label="Temperaturverlauf; Einzelwerte stehen in der Tabelle darunter"><title>Temperaturverlauf über die Zeit</title>`;
  for (let i = 0; i <= 4; i++) {
    const v = lo + ((hi - lo) * i) / 4;
    svg += `<line x1="${p}" y1="${y(v)}" x2="${w - p}" y2="${y(v)}" stroke="#26435e"/><text x="3" y="${y(v) + 4}">${v.toFixed(1)}°</text>`;
  }
  svg += `<polyline fill="none" stroke="#67b5ff" stroke-width="2" points="${rows.map((r, i) => `${x(i)},${y(r.value)}`).join(" ")}"/>`;
  rows.forEach(
    (r, i) =>
      (svg += `<circle cx="${x(i)}" cy="${y(r.value)}" r="${r.outside ? 4 : 2.5}" fill="${r.outside ? "#ffb45e" : "#86c7ff"}"><title>${esc(date(r.measured_at) + " · " + num(r.value) + " °C · " + r.actor)}</title></circle>`),
  );
  return (
    svg +
    `<text x="${p}" y="${h - 12}">${esc(date(rows[0].measured_at))}</text><text text-anchor="end" x="${w - p}" y="${h - 12}">${esc(date(rows.at(-1).measured_at))}</text></svg>`
  );
}
function deviceEditor(d = null) {
  openEditor(
    d ? "Gerät und Grenzwerte" : "Kühlgerät anlegen",
    field("name", "Name", "text", d?.name || "", 'required maxlength="150"') +
      field(
        "location",
        "Standort",
        "text",
        d?.location || "",
        'required maxlength="180"',
      ) +
      select("kind", "Geräteart", ["Kühllager", "Tiefkühllager"], d?.kind) +
      field(
        "lower",
        "Untergrenze °C",
        "number",
        d?.lower ?? 0,
        'required step="0.001" min="-100" max="150"',
      ) +
      field(
        "upper",
        "Obergrenze °C",
        "number",
        d?.upper ?? 7,
        'required step="0.001" min="-100" max="150"',
      ) +
      `<label><input type="checkbox" name="active" ${d?.active !== false ? "checked" : ""}> Aktiv</label><p class="muted">Neue Grenzen gelten für künftige Messungen. Historische Messungen behalten ihre Grenzen.</p>`,
    async (f) => {
      const body = Object.fromEntries(f);
      body.active = f.has("active");
      await api("/devices" + (d ? "/" + d.id : ""), d ? "PUT" : "POST", body);
      await show("temperatures");
    },
  );
}
function localTime() {
  const d = new Date();
  return new Date(d.getTime() - d.getTimezoneOffset() * 60000)
    .toISOString()
    .slice(0, 16);
}
async function readingEditor(d) {
  const opts = await api("/events");
  openEditor(
    "Temperatur: " + d.name,
    field(
      "value",
      "Temperatur °C",
      "number",
      "",
      'required step="0.001" min="-100" max="150"',
    ) +
      field(
        "measured_at",
        "Kontrollzeit (lokale Zeitzone)",
        "datetime-local",
        localTime(),
        "required",
      ) +
      select(
        "event_id",
        "Optionaler Vorgangsbezug",
        [["", "Keiner"], ...opts.map((e) => [e.id, e.name])],
        "",
      ) +
      '<p class="notice">Nach dem Speichern ist diese Messung unveränderbar. Bitte Wert und Zeitpunkt prüfen.</p>',
    async (f) => {
      const body = Object.fromEntries(f);
      body.device_id = d.id;
      body.measured_at = new Date(body.measured_at).toISOString();
      body.event_id = body.event_id ? Number(body.event_id) : null;
      body.external_id = crypto.randomUUID();
      await api("/readings", "POST", body);
      await show("temperatures");
    },
    "Unveränderbar speichern",
  );
}
async function exportEditor(d) {
  const opts = await api("/events");
  openEditor(
    "Temperaturen als Spez71 exportieren",
    field("start", "Von (lokale Zeitzone)", "datetime-local", "", "required") +
      field(
        "end",
        "Bis (lokale Zeitzone)",
        "datetime-local",
        localTime(),
        "required",
      ) +
      select(
        "interval_hours",
        "Maximal ein Messwert je Zeitfenster",
        [1, 2, 3, 6, 12, 24].map((n) => [n, n + " Stunde(n)"]),
        1,
      ) +
      select(
        "event_id",
        "Optionaler Vorgang",
        [["", "Eigenständiger Nachweis"], ...opts.map((e) => [e.id, e.name])],
        "",
      ) +
      '<label><input type="checkbox" name="include_notes" checked> Bemerkungen übernehmen</label><p class="muted">Je Zeitfenster ab Beginn wird der erste tatsächliche Messwert übernommen. Der Nachweis bleibt zunächst ein Entwurf. Unterschriften bleiben leer.</p>',
    async (f) => {
      const body = Object.fromEntries(f);
      body.start = new Date(body.start).toISOString();
      body.end = new Date(body.end).toISOString();
      body.interval_hours = Number(body.interval_hours);
      body.include_notes = f.has("include_notes");
      body.event_id = body.event_id ? Number(body.event_id) : null;
      const r = await api("/devices/" + d.id + "/export", "POST", body);
      formRecord = r;
      await show("proof");
      message(
        `${r.selected_count} von ${r.total_count} Messwerten übernommen. ${r.omitted_outliers} Grenzwertverletzungen durch die gewählte Frequenz nicht enthalten. Originalwerte bleiben gespeichert.`,
        r.omitted_outliers > 0,
      );
    },
    "Nachweis erstellen",
  );
}
async function renderFiles() {
  await filesInto($("#workspace"));
}
async function filesInto(
  target,
  eventId = null,
  templateId = null,
  locked = false,
) {
  const query = eventId
    ? "?event_id=" + eventId
    : templateId
      ? "?template_id=" + templateId
      : "";
  const files = await api("/documents" + query);
  target.innerHTML = `<div class="panel"><h2>${eventId ? "Vorgangsdokumente" : templateId ? "Vorlagendokumente" : "Zentrale Dateiablage"}</h2><p class="muted">PDF-Dokumente${templateId ? " werden beim Erstellen in den Vorgang übernommen" : ""}. Upload bis 5 MB, Administrator bis 50 MB.</p>${kitchen() && !locked ? '<label class="import-label">PDF hinzufügen<input type="file" accept="application/pdf" id="file-upload"></label>' : ""}${
    files.length
      ? table(
          ["Datei", "Hinzugefügt", "Aktionen"],
          files.map(
            (d) =>
              `<tr data-id="${d.id}"><td class="file-name">${esc(d.name)}<small>${num(d.size / 1024)} KB</small></td><td>${esc(d.actor)}<small>${date(d.at)}</small></td><td><a class="btn subtle" href="/api/documents/${d.id}/download">Herunterladen</a>${kitchen() && !locked ? btn("Entfernen", "delete", "danger") : ""}</td></tr>`,
          ),
        )
      : empty("Noch keine Dokumente abgelegt.")
  }</div>`;
  const input = target.querySelector("#file-upload");
  if (input)
    input.onchange = async () => {
      const file = input.files[0];
      if (!file) return;
      try {
        const f = new FormData();
        f.append("file", file);
        if (eventId) f.append("event_id", eventId);
        if (templateId) f.append("template_id", templateId);
        await api("/documents", "POST", f);
        await filesInto(target, eventId, templateId, locked);
      } catch (e) {
        fail(e);
      }
    };
  bind(
    {
      delete: async (b) => {
        if (confirm("Dieses Dokument entfernen?")) {
          await api("/documents/" + b.closest("tr").dataset.id, "DELETE");
          await filesInto(target, eventId, templateId, locked);
        }
      },
    },
    target,
  );
}
async function renderForms() {
  await formsInto($("#workspace"));
}
async function formsInto(target, eventId = null, locked = false) {
  const rows = await api("/forms" + (eventId ? "?event_id=" + eventId : ""));
  const types = await api("/form-templates");
  target.innerHTML = `<div class="bar"><h2>${eventId ? "Nachweise zum Vorgang" : "Eigenständige Nachweise"}</h2>${!locked ? btn("＋ Spez71 hinzufügen", "new", "primary") : ""}</div>${!eventId ? '<p class="muted">Wareneingang, Lagerhygiene und Temperaturexporte. Weitere Checklisten werden direkt einem Vorgang hinzugefügt.</p>' : ""}${
    rows.length
      ? table(
          ["Nachweis", "Vorlage", "Status", ""],
          rows.map(
            (f) =>
              `<tr data-id="${f.id}"><td>${esc(f.title)}${f.correction_of ? `<small>Berichtigung zu #${f.correction_of}</small>` : ""}</td><td>${esc(types.find((t) => t.id === f.template_id)?.code)}<small>${esc(types.find((t) => t.id === f.template_id)?.version)}</small></td><td>${f.finalized_at ? "Final gespeichert · " + date(f.finalized_at) : "Entwurf"}</td><td>${btn("Öffnen", "open")}<a class="btn subtle" href="/api/forms/${f.id}/pdf">PDF</a></td></tr>`,
          ),
        )
      : empty("Noch keine Nachweise angelegt.")
  }`;
  bind(
    {
      new: () =>
        openEditor(
          "Spez71 hinzufügen",
          field(
            "title",
            "Bezeichnung",
            "text",
            "",
            'required maxlength="150"',
          ) +
            select(
              "template_id",
              "Originalvorlage",
              types
                .filter(
                  (t) =>
                    t.active &&
                    (eventId ||
                      [
                        "Spez71-04",
                        "Spez71-05",
                        "Spez71-06",
                        "Spez71-07",
                      ].includes(t.code)),
                )
                .map((t) => [
                  t.id,
                  t.code + " · " + t.name + " · " + t.version,
                ]),
            ) +
            field(
              "correction_of",
              "Optional: Nummer des zu berichtigenden Nachweises",
              "number",
              "",
              'min="1"',
            ),
          async (f) => {
            const d = Object.fromEntries(f);
            d.template_id = Number(d.template_id);
            d.event_id = eventId;
            d.correction_of = d.correction_of ? Number(d.correction_of) : null;
            formRecord = await api("/forms", "POST", d);
            await show("proof");
          },
        ),
      open: async (b) => {
        formRecord = { id: Number(b.closest("tr").dataset.id) };
        await show("proof");
      },
    },
    target,
  );
}
async function renderProof() {
  formRecord = await api("/forms/" + formRecord.id);
  const f = formRecord;
  let locked = !!f.finalized_at;
  if (f.event_id) {
    const e = await api("/events/" + f.event_id);
    locked = locked || !!e.deleted_at;
  }
  const pages = [...f.layout.pages];
  for (let i = 0; i < f.extra_pages; i++)
    pages.push(f.layout.pages[f.layout.repeat_page]);
  $("#workspace").innerHTML =
    `<div class="bar">${btn("← Zur Übersicht", "back")}<h2>${esc(f.title)}</h2></div><p class="muted">${esc(f.template_name)} · ${esc(f.template_version)} · Nachweis #${f.id}${f.correction_of ? " · Berichtigung zu #" + f.correction_of : ""}</p>${f.finalized_at ? `<div class="final-badge">Final gespeichert am ${date(f.finalized_at)} durch ${esc(f.finalized_by)}. Auch Administratoren können diesen Nachweis nicht verändern.</div>` : ""}<div class="proof-tools bar">${!locked ? btn("Entwurf speichern", "save", "primary") : ""}${!locked && f.layout.repeat_page !== null ? btn("＋ Originalblatt", "page") : ""}${!locked && lead() ? btn("Final Speichern", "final", "danger") : ""}<a class="btn subtle" href="/api/forms/${f.id}/pdf">PDF herunterladen</a></div><p class="proof-help">Direkt in den blau hinterlegten Feldern des Originals ausfüllen. Unterschrifts- und Handzeichenfelder bleiben für Papier frei. Vor dem Finalisieren den Entwurf speichern und das PDF prüfen.</p><div class="proof-scroll"><div class="proof-pages">${pages
      .map(
        (p, i) =>
          `<div class="form-sheet" data-page="${i}" style="aspect-ratio:${p.width}/${p.height}"><img src="/api/forms/${f.id}/pages/${i}?revision=${f.revision}" alt="Nachweis Seite ${i + 1}" loading="lazy">${(f.finalized_at
            ? []
            : p.fields
          )
            .map((field) => {
              const key = i + ":" + field.id,
                r = field.rect,
                style = `left:${(r[0] / p.width) * 100}%;top:${(r[1] / p.height) * 100}%;width:${((r[2] - r[0]) / p.width) * 100}%;height:${((r[3] - r[1]) / p.height) * 100}%;font-size:${(Math.min(11, r[3] - r[1] - 2) / p.width) * 100}cqw;`;
              return field.kind === "checkbox"
                ? `<input class="pdf-field" type="checkbox" data-key="${key}" aria-label="Seite ${i + 1}: ${esc(field.label)}" title="${esc(field.label)}" style="${style}" ${f.values[key] ? "checked" : ""} ${locked ? "disabled" : ""}>`
                : `<textarea class="pdf-field" data-key="${key}" aria-label="Seite ${i + 1}: ${esc(field.label)}" title="${esc(field.label)}" style="${style}" maxlength="2000" ${locked ? "disabled" : ""}>${esc(f.values[key] || "")}</textarea>`;
            })
            .join("")}</div>`,
      )
      .join("")}</div></div>`;
  $$(".form-sheet").forEach((p) => (p.style.containerType = "inline-size"));
  const collect = () =>
    Object.fromEntries(
      $$(".pdf-field").map((el) => [
        el.dataset.key,
        el.type === "checkbox" ? el.checked : el.value,
      ]),
    );
  async function save(extra = f.extra_pages, values = collect()) {
    const r = await api("/forms/" + f.id, "PUT", {
      values,
      extra_pages: extra,
      revision: f.revision,
    });
    Object.assign(f, r);
    return r;
  }
  bind({
    back: async () => {
      if (f.event_id) {
        active = { id: f.event_id };
        await show("detail");
      } else await show("forms");
    },
    save: async () => {
      await save();
      message("Entwurf gespeichert.");
    },
    page: async () => {
      const values = collect(),
        pi = pages.length,
        original = f.layout.pages[f.layout.repeat_page];
      for (const field of original.fields) {
        if (!("row" in field))
          values[pi + ":" + field.id] =
            values[f.layout.repeat_page + ":" + field.id] || "";
      }
      await save(f.extra_pages + 1, values);
      await renderProof();
    },
    final: async () => {
      await save();
      if (
        confirm(
          "Diesen Nachweis endgültig sperren? Danach kann niemand – auch kein Administrator – Änderungen vornehmen. Unterschriften bleiben leer.",
        )
      ) {
        await api("/forms/" + f.id + "/finalize", "POST", {
          revision: f.revision,
        });
        await renderProof();
      }
    },
  });
}
async function renderUsers() {
  const [users, keys] = await Promise.all([api("/users"), api("/api-keys")]);
  $("#workspace").innerHTML =
    `<div class="bar"><h2>Benutzerverwaltung</h2>${btn("＋ Benutzer", "new", "primary")}</div>${table(
      ["Benutzer", "Rolle", "Status", "Aktionen"],
      users.map(
        (u) =>
          `<tr data-id="${u.id}"><td>${esc(u.username)}</td><td>${roles[u.role]}</td><td>${u.active ? "Aktiv" : "Deaktiviert"}</td><td>${btn("Rolle / Status", "edit")}${btn("Passwort", "password")}</td></tr>`,
      ),
    )}<div class="panel"><div class="bar"><h2>Technische Zugänge</h2>${btn("＋ API-Schlüssel", "key", "primary")}</div><p class="muted">Eigener technischer Benutzer pro Integration empfohlen. Schlüssel werden nur bei Erstellung einmal angezeigt.</p>${table(
      ["Name", "Benutzer", "Rechte / Geräte", "Status", ""],
      keys.map(
        (k) =>
          `<tr data-id="${k.id}"><td>${esc(k.name)}</td><td>${esc(users.find((u) => u.id === k.user_id)?.username)}</td><td>${esc(k.scopes.join(", "))}<small>${k.device_ids.length ? "Geräte: " + k.device_ids.join(", ") : "Alle Geräte"}</small></td><td>${k.active ? "Aktiv" : "Widerrufen"}</td><td>${k.active ? btn("Widerrufen", "revoke", "danger") : ""}</td></tr>`,
      ),
    )}</div>`;
  bind({
    new: () =>
      openEditor(
        "Benutzer anlegen",
        field(
          "username",
          "Benutzername",
          "text",
          "",
          'required maxlength="80"',
        ) +
          field(
            "password",
            "Passwort (mindestens 12 Zeichen)",
            "password",
            "",
            'required minlength="12"',
          ) +
          select("role", "Rolle", Object.entries(roles), "user"),
        async (f) => {
          await api("/users", "POST", Object.fromEntries(f));
          await show("users");
        },
      ),
    edit: (b) => {
      const u = users.find((u) => u.id === Number(b.closest("tr").dataset.id));
      openEditor(
        u.username,
        select("role", "Rolle", Object.entries(roles), u.role) +
          `<label><input type="checkbox" name="active" ${u.active ? "checked" : ""}> Aktiv</label>`,
        async (f) => {
          await api("/users/" + u.id, "PATCH", {
            role: f.get("role"),
            active: f.has("active"),
          });
          await show("users");
        },
      );
    },
    password: (b) =>
      openEditor(
        "Passwort ändern",
        field(
          "password",
          "Neues Passwort",
          "password",
          "",
          'required minlength="12"',
        ),
        async (f) => {
          await api(
            "/users/" + b.closest("tr").dataset.id,
            "PATCH",
            Object.fromEntries(f),
          );
          await show("users");
        },
      ),
    key: async () => {
      const ds = await api("/devices");
      openEditor(
        "API-Schlüssel erstellen",
        field("name", "Name der Integration", "text", "", "required") +
          select(
            "user_id",
            "Technischer Benutzer",
            users
              .filter((u) => u.role === "technical" && u.active)
              .map((u) => [u.id, u.username]),
          ) +
          "<h3>Freigegebene Funktionen</h3>" +
          [
            ["stock:read", "Bestände lesen"],
            ["stock:write", "Ein- und Ausgänge buchen"],
            ["temperature:write", "Temperaturen senden"],
          ]
            .map(
              ([v, l]) =>
                `<label><input type="checkbox" name="scopes" value="${v}">${l}</label>`,
            )
            .join("") +
          '<h3>Geräte beschränken</h3><p class="muted">Ohne Auswahl sind alle Geräte freigegeben.</p>' +
          ds
            .map(
              (d) =>
                `<label><input type="checkbox" name="device_ids" value="${d.id}">${esc(d.name)}</label>`,
            )
            .join(""),
        async (f) => {
          const k = await api("/api-keys", "POST", {
            name: f.get("name"),
            user_id: Number(f.get("user_id")),
            scopes: f.getAll("scopes"),
            device_ids: f.getAll("device_ids").map(Number),
          });
          await show("users");
          setTimeout(
            () =>
              openEditor(
                "API-Schlüssel – einmalige Anzeige",
                `<p>Jetzt sicher übernehmen. Dieser Schlüssel kann später nicht erneut angezeigt werden.</p><pre class="token">${esc(k.token)}</pre><p class="muted">Als Authorization: Bearer … senden. Schnittstellenbeschreibung: docs/API.md im Projekt.</p>`,
                null,
              ),
            0,
          );
        },
      );
    },
    revoke: async (b) => {
      if (confirm("API-Schlüssel sofort widerrufen?")) {
        await api("/api-keys/" + b.closest("tr").dataset.id, "DELETE");
        await show("users");
      }
    },
  });
}
async function renderSettings() {
  const config = await api("/settings");
  const types = await api("/form-templates");
  $("#workspace").innerHTML =
    `<div class="panel"><h2>Ortsverband & Logo</h2><form id="settings-form">${field("ov_name", "OV-Bezeichnung", "text", config.ov_name, 'required maxlength="150"')}<div class="bar"><button class="btn primary">OV-Name speichern</button></div></form><label class="import-label">Logo ersetzen (PNG oder JPEG, maximal 5 MB)<input type="file" id="logo-upload" accept="image/png,image/jpeg"></label><p class="muted">Das Logo wird unverzerrt auf Anmeldung und Navigation gezeigt. Originalformulare bleiben unverändert.</p></div><div class="panel"><div class="bar"><h2>Spez71-Originalvorlagen</h2>${btn("Neue Vorlagenversion", "version", "primary")}</div><p class="muted">Vorhandene Nachweise behalten ihre Originalvorlage. Neue Versionen benötigen das Original-PDF und ein dazu passendes Feldlayout.</p>${table(
      ["Vorlage", "Version", "Status"],
      types.map(
        (t) =>
          `<tr><td>${esc(t.code)} · ${esc(t.name)}</td><td>${esc(t.version)}</td><td>${t.active ? "Aktiv" : "Vorversion"}</td></tr>`,
      ),
    )}</div>`;
  $("#settings-form").onsubmit = async (e) => {
    e.preventDefault();
    try {
      await api("/settings", "PUT", Object.fromEntries(new FormData(e.target)));
      await loadSettings();
      message("OV-Konfiguration gespeichert.");
    } catch (err) {
      fail(err);
    }
  };
  $("#logo-upload").onchange = async (e) => {
    if (!e.target.files[0]) return;
    try {
      const f = new FormData();
      f.append("file", e.target.files[0]);
      await api("/settings/logo", "PUT", f);
      await loadSettings();
      message("Logo aktualisiert.");
    } catch (err) {
      fail(err);
    }
  };
  bind({
    version: () =>
      openEditor(
        "Neue Originalvorlage hinterlegen",
        field(
          "code",
          "Formularnummer",
          "text",
          "Spez71-",
          'required maxlength="20"',
        ) +
          field("name", "Bezeichnung", "text", "", 'required maxlength="200"') +
          field(
            "version",
            "Versionsstand",
            "text",
            "",
            'required maxlength="100"',
          ) +
          '<label>Original-PDF<input type="file" name="file" accept="application/pdf" required></label>' +
          area(
            "layout",
            "Feldlayout (JSON aus der geprüften Vorlagenkonfiguration)",
            "",
            "required",
          ) +
          '<p class="muted">Format und Anleitung: docs/FORMULARE.md. Die bisherige Version bleibt für bestehende Nachweise erhalten.</p>',
        async (f) => {
          await api("/form-templates", "POST", f);
          await show("settings");
        },
      ),
  });
}
async function renderAudit() {
  const rows = await api("/audit");
  $("#workspace").innerHTML =
    `<div class="panel"><h2>Änderungsprotokoll</h2><p class="muted">Letzte 500 Aktionen. Lagerbuchungen und Messwerte besitzen zusätzlich ihre eigene unveränderbare Historie.</p>${table(
      ["Zeit", "Benutzer", "Aktion", "Referenz"],
      rows.map(
        (r) =>
          `<tr><td>${date(r.at)}</td><td>${esc(r.actor)}</td><td>${esc(r.action)}</td><td>${esc(r.entity)}</td></tr>`,
      ),
    )}</div>`;
}
(async () => {
  try {
    await loadSettings();
    const r = await api("/auth/me");
    user = r.user;
    csrf = r.csrf;
    showApp();
    await show("events");
  } catch {
    logoutUI();
  }
})();
