const submitDialog = document.querySelector("#submit-event-dialog");
const submitForm = document.querySelector("#missing-event-form");
const submitStatus = document.querySelector("#submit-status");
const submitUrl = document.querySelector("#missing-event-url");
const submitName = document.querySelector("#missing-event-name");
const submitAudience = document.querySelector("#missing-event-audience");
const submitNotes = document.querySelector("#missing-event-notes");

function openSubmitDialog() {
  submitStatus.textContent = "";
  submitDialog.showModal();
  window.setTimeout(() => submitUrl.focus(), 0);
}

function closeSubmitDialog() {
  submitDialog.close();
}

function showFallbackLink(issueUrl) {
  submitStatus.textContent = "Your browser blocked the new tab. ";
  const link = document.createElement("a");
  link.href = issueUrl;
  link.target = "_blank";
  link.rel = "noopener noreferrer";
  link.textContent = "Open the submission page";
  submitStatus.append(link, ".");
}

document.querySelectorAll("[data-open-submit]").forEach((button) => {
  button.addEventListener("click", openSubmitDialog);
});

document.querySelector("#close-submit-event").addEventListener("click", closeSubmitDialog);
submitDialog.addEventListener("click", (event) => {
  if (event.target === submitDialog) closeSubmitDialog();
});

submitForm.addEventListener("submit", async (event) => {
  event.preventDefault();

  const eventUrl = submitUrl.value.trim();
  const eventName = submitName.value.trim();
  const audience = submitAudience.value;
  const notes = submitNotes.value.trim();

  let parsedUrl;
  try {
    parsedUrl = new URL(eventUrl);
    if (!["http:", "https:"].includes(parsedUrl.protocol)) throw new Error("Unsupported protocol");
  } catch {
    submitStatus.textContent = "Enter a complete public link beginning with http:// or https://.";
    submitUrl.focus();
    return;
  }

  const title = `Missing event: ${eventName || parsedUrl.hostname}`;
  const body = [
    "## Event link",
    eventUrl,
    "",
    "## Event name",
    eventName || "Not provided",
    "",
    "## Audience or age restriction",
    audience,
    "",
    "## Notes",
    notes || "No additional notes provided.",
    "",
    "## Review required",
    "- Verify the organizer or venue source",
    "- Check for duplicates and geographic relevance",
    "- Check age restrictions and content warnings",
    "- Apply the family-friendly content policy",
    "- Reject malware, scams, illegal activity, pornography, explicit sexual content, hate promotion, or unsafe material",
    "- Base the decision on event content, age suitability, source credibility, legality, local relevance, and safety",
    "- Standard submission is free; sponsorship never bypasses review",
  ].join("\n");

  const issueUrl = new URL("https://github.com/agr77one/warsaw-events/issues/new");
  issueUrl.searchParams.set("template", "missing-event.md");
  issueUrl.searchParams.set("title", title);
  issueUrl.searchParams.set("body", body);

  try {
    await navigator.clipboard.writeText(`${title}\n\n${body}`);
    submitStatus.textContent = "Submission copied. GitHub is opening so you can review and send it.";
  } catch {
    submitStatus.textContent = "GitHub is opening so you can review and send the event link.";
  }

  const opened = window.open(issueUrl.href, "_blank");
  if (opened) {
    opened.opener = null;
  } else {
    showFallbackLink(issueUrl.href);
  }
});
