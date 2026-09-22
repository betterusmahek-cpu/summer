(() => {
  "use strict";

  const state = {
    questions: [],
    sections: [],
    scales: {},
    order: [],          // list of question objects in display order
    answers: {},         // qid -> value
    index: 0,             // current position in state.order
    useLLM: false,         // whether to request LLM-assisted extraction
  };

  const els = {};

  function qs(id) { return document.getElementById(id); }

  function cacheEls() {
    els.screenIntro = qs("screen-intro");
    els.screenQuestionnaire = qs("screen-questionnaire");
    els.screenLoading = qs("screen-loading");
    els.screenResults = qs("screen-results");
    els.screenError = qs("screen-error");

    els.btnStart = qs("btn-start");
    els.useLLMCheckbox = qs("use-llm-checkbox");
    els.btnBack = qs("btn-back");
    els.btnNext = qs("btn-next");
    els.qForm = qs("q-form");
    els.qText = qs("q-text");
    els.qScale = qs("q-scale");
    els.qSectionTitle = qs("q-section-title");
    els.qCounter = qs("q-counter");
    els.progressFill = qs("progress-fill");
    els.progressTrack = qs("progress-track");
    els.qError = qs("q-error");
    els.railList = qs("rail-list");

    els.extractionMethodNote = qs("extraction-method-note");
    els.loadingLabel = qs("screen-loading").querySelector(".loading-label");

    els.resultsLabel = qs("results-label");
    els.resultsConfidence = qs("results-confidence");
    els.resultsDisclaimer = qs("results-disclaimer");
    els.resultsRationale = qs("results-rationale");
    els.signalPanel = qs("signal-panel");
    els.contributingWrap = qs("contributing-wrap");
    els.contributingList = qs("contributing-list");
    els.btnRestart = qs("btn-restart");

    els.errorMessage = qs("error-message");
    els.btnErrorRetry = qs("btn-error-retry");
  }

  function showScreen(name) {
    [els.screenIntro, els.screenQuestionnaire, els.screenLoading, els.screenResults, els.screenError]
      .forEach((el) => el.classList.add("is-hidden"));
    name.classList.remove("is-hidden");
    window.scrollTo({ top: 0, behavior: "instant" in window ? "instant" : "auto" });
  }

  async function loadQuestions() {
    const res = await fetch("/api/questions");
    if (!res.ok) throw new Error("failed to load questionnaire");
    const data = await res.json();
    state.questions = data.questions;
    state.sections = data.sections;
    state.scales = data.scales;
    state.order = data.questions.slice().sort((a, b) => a.id - b.id);
    buildRail();
  }

  function buildRail() {
    els.railList.innerHTML = "";
    state.sections.forEach((section) => {
      const li = document.createElement("li");
      li.className = "rail-item";
      li.dataset.sectionId = section.id;
      li.textContent = section.title;
      els.railList.appendChild(li);
    });
  }

  function sectionForQuestion(q) {
    return state.sections.find((s) => s.id === q.domain);
  }

  function updateRail(currentSectionId) {
    const items = els.railList.querySelectorAll(".rail-item");
    const currentSectionIndex = state.sections.findIndex((s) => s.id === currentSectionId);
    items.forEach((item, i) => {
      item.classList.toggle("is-active", i === currentSectionIndex);
      item.classList.toggle("is-done", i < currentSectionIndex);
    });
  }

  function renderQuestion() {
    const q = state.order[state.index];
    const section = sectionForQuestion(q);
    const scaleOptions = state.scales[q.scale];

    els.qSectionTitle.textContent = section ? section.title : "Section";
    els.qText.textContent = q.text;
    els.qCounter.textContent = `Question ${state.index + 1} of ${state.order.length}`;

    const pct = Math.round((state.index / state.order.length) * 100);
    els.progressFill.style.width = pct + "%";
    els.progressTrack.setAttribute("aria-valuenow", String(pct));

    updateRail(q.domain);

    els.qScale.innerHTML = "";
    const groupName = `q-${q.id}`;
    const selectedValue = state.answers[q.id];

    scaleOptions.forEach((opt) => {
      const wrapper = document.createElement("label");
      wrapper.className = "scale-option";
      if (selectedValue === opt.value) wrapper.classList.add("is-selected");

      const input = document.createElement("input");
      input.type = "radio";
      input.name = groupName;
      input.value = String(opt.value);
      input.className = "visually-hidden-radio";
      input.style.position = "absolute";
      input.style.opacity = "0";
      input.style.pointerEvents = "none";
      if (selectedValue === opt.value) input.checked = true;

      input.addEventListener("change", () => {
        state.answers[q.id] = opt.value;
        els.qError.classList.add("is-hidden");
        Array.from(els.qScale.querySelectorAll(".scale-option")).forEach((el) =>
          el.classList.remove("is-selected")
        );
        wrapper.classList.add("is-selected");
      });

      const tick = document.createElement("span");
      tick.className = "scale-tick";
      tick.textContent = String(opt.value);
      tick.setAttribute("aria-hidden", "true");

      const label = document.createElement("span");
      label.className = "scale-label";
      label.textContent = opt.label;

      wrapper.appendChild(input);
      wrapper.appendChild(tick);
      wrapper.appendChild(label);
      els.qScale.appendChild(wrapper);
    });

    els.btnBack.disabled = state.index === 0;
    els.btnNext.textContent = state.index === state.order.length - 1 ? "See results" : "Next";
  }

  function handleNext(e) {
    e.preventDefault();
    const q = state.order[state.index];
    if (state.answers[q.id] === undefined) {
      els.qError.classList.remove("is-hidden");
      return;
    }
    if (state.index < state.order.length - 1) {
      state.index += 1;
      renderQuestion();
    } else {
      submitScreening();
    }
  }

  function handleBack() {
    if (state.index > 0) {
      state.index -= 1;
      renderQuestion();
    }
  }

  async function submitScreening() {
    els.loadingLabel.textContent = state.useLLM
      ? "Extracting domain features via LLM\u2026"
      : "Scoring domain features\u2026";
    showScreen(els.screenLoading);
    try {
      const res = await fetch("/api/screen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ answers: state.answers, use_llm: state.useLLM }),
      });

      let data;
      try {
        data = await res.json();
      } catch (_) {
        data = null;
      }

      if (!res.ok) {
        const message = (data && data.error) || "The scoring service returned an error.";
        showError(message);
        return;
      }

      renderResults(data);
      showScreen(els.screenResults);
    } catch (err) {
      showError("We couldn't reach the scoring service. Check your connection and try again.");
    }
  }

  function showError(message) {
    els.errorMessage.textContent = message;
    showScreen(els.screenError);
  }

  const DOMAIN_LABELS = {
    attention: "Attention",
    hyperactivity: "Hyperactivity",
    impulsivity: "Impulsivity",
    childhood_history: "Childhood history",
    memory: "Memory",
    executive: "Executive function",
    language: "Language",
    orientation: "Orientation",
    cognitive_decline: "Cognitive decline",
    functional_impairment: "Functional impairment",
  };

  const EXTRACTION_METHOD_NOTES = {
    llm: "Domain features for this result were extracted by the LLM-assisted step.",
    deterministic: "Domain features for this result were computed deterministically (LLM extraction was not requested).",
    deterministic_fallback_no_api_key:
      "LLM extraction was requested, but no API key is configured on the server \u2014 this result used deterministic scoring instead.",
    deterministic_fallback_llm_error:
      "LLM extraction was requested but failed, so this result used deterministic scoring as a fallback.",
  };

  function renderResults(data) {
    els.resultsLabel.textContent = data.label;
    els.resultsConfidence.textContent = Math.round(data.confidence * 100) + "%";
    els.resultsDisclaimer.textContent = data.disclaimer;
    els.resultsRationale.textContent = data.rationale;

    const method = data.meta && data.meta.extraction_method;
    const note = EXTRACTION_METHOD_NOTES[method];
    if (note) {
      els.extractionMethodNote.textContent = note;
      els.extractionMethodNote.classList.remove("is-hidden");
    } else {
      els.extractionMethodNote.classList.add("is-hidden");
    }

    els.signalPanel.innerHTML = "";
    const domainEntries = Object.entries(data.domains || {});
    domainEntries.forEach(([key, value]) => {
      const row = document.createElement("div");
      row.className = "signal-row";

      const name = document.createElement("span");
      name.className = "signal-name";
      name.textContent = DOMAIN_LABELS[key] || key;

      const track = document.createElement("div");
      track.className = "signal-track";
      const fill = document.createElement("div");
      fill.className = "signal-fill" + (value >= 2.5 ? " is-elevated" : "");
      fill.style.width = Math.min(100, (value / 4) * 100) + "%";
      track.appendChild(fill);

      const val = document.createElement("span");
      val.className = "signal-value";
      val.textContent = value.toFixed(1);

      row.appendChild(name);
      row.appendChild(track);
      row.appendChild(val);
      els.signalPanel.appendChild(row);
    });

    const contributing = data.contributing_domains || [];
    if (contributing.length) {
      els.contributingWrap.classList.remove("is-hidden");
      els.contributingList.innerHTML = "";
      contributing.forEach((key) => {
        const li = document.createElement("li");
        li.textContent = DOMAIN_LABELS[key] || key;
        els.contributingList.appendChild(li);
      });
    } else {
      els.contributingWrap.classList.add("is-hidden");
    }
  }

  function restart() {
    state.answers = {};
    state.index = 0;
    if (els.useLLMCheckbox) els.useLLMCheckbox.checked = false;
    state.useLLM = false;
    showScreen(els.screenIntro);
  }

  async function init() {
    cacheEls();

    els.btnStart.addEventListener("click", async () => {
      els.btnStart.disabled = true;
      state.useLLM = !!(els.useLLMCheckbox && els.useLLMCheckbox.checked);
      try {
        if (!state.order.length) {
          await loadQuestions();
        }
        state.index = 0;
        showScreen(els.screenQuestionnaire);
        renderQuestion();
      } catch (err) {
        showError("We couldn't load the questionnaire. Please refresh and try again.");
      } finally {
        els.btnStart.disabled = false;
      }
    });

    els.qForm.addEventListener("submit", handleNext);
    els.btnBack.addEventListener("click", handleBack);
    els.btnRestart.addEventListener("click", restart);
    els.btnErrorRetry.addEventListener("click", () => showScreen(els.screenIntro));

    // Warm the questionnaire in the background so "Begin screening" feels instant.
    loadQuestions().catch(() => {
      /* silent -- will retry on click if this failed */
    });
  }

  document.addEventListener("DOMContentLoaded", init);
})();
