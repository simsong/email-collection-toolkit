// Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
// Bind the welcome button to the shared Python document-opening action.
// A standard system file picker runs without granting the page filesystem access.
// Pending and failed operations remain visible in this window.
// Creation uses the common destination picker and explicit import confirmation.
// Existing document validation determines whether a collection can open.
const openButton = document.getElementById("open");
const newButton = document.getElementById("new");
const status = document.getElementById("status");
window.addEventListener("pywebviewready", () => {
  openButton.disabled = false;
  newButton.disabled = false;
  status.textContent = "";
});
newButton.addEventListener("click", async () => {
  newButton.disabled = true;
  openButton.disabled = true;
  try {
    await window.pywebview.api.new_document();
  } catch (error) { status.textContent = String(error); }
  finally { newButton.disabled = false; openButton.disabled = false; }
});
openButton.addEventListener("click", async () => {
  openButton.disabled = true;
  try {
    const opened = await window.pywebview.api.open_archive_dialog();
    status.textContent = opened ? "" : "No archive opened. Choose a file inside a valid collection; details are in Help → About.";
  } catch (error) { status.textContent = String(error); }
  finally { openButton.disabled = false; }
});
