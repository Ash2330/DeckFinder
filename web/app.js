const $ = (selector, root = document) => root.querySelector(selector);
const notice = $("#notice");
const resultList = $("#results");
const editor = $("#editor");
const slidesNode = $("#slides");
let cfg;
let token = sessionStorage.getItem("deckfinder.token");
let draftId;
let slides = [];

function message(text, error = false) {
  notice.textContent = text;
  notice.dataset.error = error;
}

function element(tag, text, className) {
  const value = document.createElement(tag);
  if (text) value.textContent = text;
  if (className) value.className = className;
  return value;
}

function button(text, handler, secondary = false) {
  const value = element("button", text, secondary ? "secondary" : "");
  value.type = "button";
  value.addEventListener("click", () => perform(value, handler));
  return value;
}

async function perform(control, handler) {
  control.disabled = true;
  message("");
  try {
    await handler();
  } catch (error) {
    message(error.message, true);
  } finally {
    control.disabled = false;
  }
}

async function api(path, options = {}) {
  const response = await fetch("/api" + path, {
    ...options,
    headers: { ...(token ? { Authorization: "Bearer " + token } : {}), ...options.headers },
  });
  if (response.status === 401) {
    clearSession();
    throw Error("Sign in to continue.");
  }
  const payload = await response.json().catch(() => null);
  if (!response.ok) {
    throw Error(typeof payload?.detail === "string" ? payload.detail : "The request could not be completed.");
  }
  return payload;
}

function base64url(bytes) {
  return btoa(String.fromCharCode(...bytes)).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
}

async function signIn() {
  const verifier = base64url(crypto.getRandomValues(new Uint8Array(48)));
  const state = base64url(crypto.getRandomValues(new Uint8Array(24)));
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(verifier));
  sessionStorage.setItem("deckfinder.pkce", JSON.stringify({ verifier, state }));
  const params = new URLSearchParams({
    response_type: "code", client_id: cfg.client, scope: "openid",
    redirect_uri: location.origin + "/", state,
    code_challenge_method: "S256", code_challenge: base64url(new Uint8Array(digest)),
  });
  location.assign(cfg.domain + "/oauth2/authorize?" + params);
}

async function completeSignIn() {
  const params = new URLSearchParams(location.search);
  if (!params.has("code") && !params.has("error")) return;
  history.replaceState(null, "", "/");
  const pending = JSON.parse(sessionStorage.getItem("deckfinder.pkce") || "null");
  sessionStorage.removeItem("deckfinder.pkce");
  if (!pending || params.get("state") !== pending.state || params.has("error")) {
    throw Error("Sign-in could not be verified. Start a new sign-in.");
  }
  const response = await fetch(cfg.domain + "/oauth2/token", {
    method: "POST",
    body: new URLSearchParams({
      grant_type: "authorization_code", client_id: cfg.client,
      code: params.get("code"), code_verifier: pending.verifier, redirect_uri: location.origin + "/",
    }),
  });
  if (!response.ok) throw Error("Sign-in failed. Try again.");
  token = (await response.json()).access_token;
  sessionStorage.setItem("deckfinder.token", token);
}

function clearSession() {
  token = null;
  sessionStorage.removeItem("deckfinder.token");
  $("#account").replaceChildren(button("Sign in", signIn));
  $("#tabs").hidden = true;
  switchView("search");
  resultList.replaceChildren();
  $("#library").replaceChildren();
  resetDraft();
}

function switchView(view) {
  for (const name of ["teacher", "search"]) $("#" + name).hidden = name !== view;
  for (const tab of $("#tabs").children) tab.setAttribute("aria-pressed", String(tab.dataset.view === view));
}

async function loadLibrary() {
  const records = await api("/library");
  const library = $("#library");
  library.replaceChildren();
  if (!records.length) library.append(element("p", "Your drafts and uploads will appear here.", "muted"));
  for (const entry of records) {
    const row = element("div", "", "card row");
    row.append(element("span", entry.title), element("span", entry.status, "muted"));
    if (entry.status === "draft") row.append(button("Edit", async () => {
      const value = await api("/drafts/" + entry.id);
      draftId = entry.id;
      editor.elements.title.value = value.title;
      editor.elements.course.value = value.course;
      slides = value.slides;
      showSlides();
      editor.scrollIntoView({ behavior: "smooth" });
    }, true));
    if (entry.status === "ready") row.append(downloadButton(entry.id));
    library.append(row);
  }
}

function downloadButton(id) {
  return button("Download PowerPoint", async () => {
    const { url } = await api("/decks/" + id + "/download");
    location.assign(url);
  }, true);
}

$("#search-form").addEventListener("submit", (event) => {
  event.preventDefault();
  perform($("button", event.currentTarget), async () => {
    const query = $("#query").value.trim();
    if (!query) return;
    resultList.replaceChildren();
    message("Searching decks…");
    const results = await api("/search?" + new URLSearchParams({ q: query }));
    message(results.length ? results.length + (results.length === 1 ? " deck found" : " decks found") : "No matching decks.");
    for (const deck of results) {
      const card = element("article", "", "card");
      const slide = "Slide " + deck.slide + (deck.slide_title ? " · " + deck.slide_title : "");
      card.append(element("h3", deck.title), element("p", [deck.course, slide].filter(Boolean).join(" · ")),
                  element("p", deck.excerpt), downloadButton(deck.id));
      resultList.append(card);
    }
  });
});

$("#tabs").addEventListener("click", (event) => {
  const tab = event.target.closest("button[data-view]");
  if (!tab) return;
  switchView(tab.dataset.view);
  if (tab.dataset.view === "teacher") perform(tab, loadLibrary);
  else message("");
});

$("#upload").addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.currentTarget;
  perform($("button", form), async () => {
    await api("/upload", { method: "POST", body: new FormData(form) });
    form.reset();
    message("PowerPoint uploaded. Indexing is in progress.");
    await loadLibrary();
  });
});

function resetDraft() {
  draftId = undefined;
  editor.reset();
  slides = [{ title: "", body: "", layout: "content", image: "" }];
  showSlides();
}

function labeled(name, input) {
  const label = element("label", name);
  label.append(input);
  return label;
}

function showSlides() {
  slidesNode.replaceChildren();
  slides.forEach((slide, index) => {
    const group = element("fieldset");
    group.append(element("legend", "Slide " + (index + 1)));
    const title = element("input");
    title.maxLength = 160;
    title.value = slide.title;
    title.oninput = () => { slide.title = title.value; };
    const body = element("textarea");
    body.rows = 4;
    body.maxLength = 1800;
    body.value = slide.body;
    body.oninput = () => { slide.body = body.value; };
    const layout = element("select");
    for (const [value, text] of [["content", "Title and content"], ["section", "Section opener"]]) {
      const option = element("option", text);
      option.value = value;
      layout.append(option);
    }
    layout.value = slide.layout;
    layout.onchange = () => { slide.layout = layout.value; };
    const image = element("input");
    image.type = "file";
    image.accept = "image/png,image/jpeg";
    image.onchange = () => {
      const file = image.files[0];
      if (!file) return;
      if (file.size > 200000 || !["image/png", "image/jpeg"].includes(file.type)) {
        message("Choose a PNG or JPEG image under 200 KB.", true);
        image.value = "";
        return;
      }
      const reader = new FileReader();
      reader.onload = () => { slide.image = reader.result; showSlides(); };
      reader.onerror = () => message("Could not read the slide image.", true);
      reader.readAsDataURL(file);
    };
    group.append(labeled("Layout", layout), labeled("Slide title", title), labeled("Slide text", body),
                 labeled("Slide image", image));
    if (slide.image) {
      const preview = element("img");
      preview.src = slide.image;
      preview.alt = "Current slide image";
      group.append(preview, button("Remove image", () => { slide.image = ""; showSlides(); }, true));
    }
    const actions = element("div", "", "actions");
    for (const [direction, text] of [[-1, "Move up"], [1, "Move down"]]) {
      if (!slides[index + direction]) continue;
      actions.append(button(text, () => {
        [slides[index], slides[index + direction]] = [slides[index + direction], slides[index]];
        showSlides();
      }, true));
    }
    if (slides.length > 1) actions.append(button("Remove slide", () => {
      slides.splice(index, 1);
      showSlides();
    }, true));
    group.append(actions);
    slidesNode.append(group);
  });
}

$("#add-slide").onclick = () => {
  if (slides.length >= 100) { message("A draft can contain up to 100 slides.", true); return; }
  slides.push({ title: "", body: "", layout: "content", image: "" });
  showSlides();
  $("fieldset:last-child input", slidesNode).focus();
};
$("#new-draft").onclick = resetDraft;
$("#refresh").onclick = (event) => perform(event.currentTarget, loadLibrary);

editor.addEventListener("submit", (event) => {
  event.preventDefault();
  const control = event.submitter;
  if (!control) return;
  perform(control, async () => {
    const draft = { title: editor.elements.title.value.trim(), course: editor.elements.course.value.trim(), slides };
    const saved = await api("/drafts" + (draftId ? "/" + draftId : ""), {
      method: draftId ? "PUT" : "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(draft),
    });
    draftId = saved.id;
    if (control.value === "publish") {
      await api("/drafts/" + draftId + "/publish", { method: "POST" });
      message("Deck published. Indexing is in progress.");
    } else {
      message("Draft saved.");
    }
    await loadLibrary();
  });
});

async function init() {
  cfg = await api("/config");
  $("#account").replaceChildren(button("Sign in", signIn));
  resetDraft();
  await completeSignIn();
  if (!token) return;
  const profile = await api("/me");
  $("#tabs").hidden = !profile.teacher;
  $("#account").replaceChildren(button("Sign out", () => {
    clearSession();
    const params = new URLSearchParams({ client_id: cfg.client, logout_uri: location.origin + "/" });
    location.assign(cfg.domain + "/logout?" + params);
  }, true));
}

init().catch((error) => message(error.message, true));
