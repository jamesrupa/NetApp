"use strict";

// Loaded last: every tool script has registered its tab loader by now.

const initialTab = location.hash.slice(1);
showTab(document.getElementById(`tab-${initialTab}`) ? initialTab : "health");
