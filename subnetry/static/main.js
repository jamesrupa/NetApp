"use strict";

// Loaded last: every tool script has registered its tab loader by now.

const initialTab = location.hash.slice(1);
showTab(document.getElementById(`tab-${initialTab}`) ? initialTab : "dashboard");

// Back/Forward buttons, or typing #speed etc. in the address bar.
window.addEventListener("popstate", () => {
  const tab = location.hash.slice(1) || "dashboard";
  if (tab !== currentTab && document.getElementById(`tab-${tab}`)) showTab(tab);
});
window.addEventListener("hashchange", () => {
  const tab = location.hash.slice(1);
  if (tab && tab !== currentTab && document.getElementById(`tab-${tab}`)) showTab(tab);
});
