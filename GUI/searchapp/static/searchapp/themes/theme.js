
(function () {
	"use strict";

	var STORAGE_KEY = "vibravid-theme";
	var DEFAULT_THEME = "default";

	var THEMES = [
		{ id: "default", label: "Default (Dark)", color: "#0a0a0a" },
		{ id: "default-light", label: "Default (Light)", color: "#ffffff" },
		{ id: "abyss", label: "Abyss", color: "#0a0a0a" },
		{ id: "abyss-ocean", label: "Abyss Ocean", color: "#06080f" },
	];

	function isValidTheme(id) {
		for (var i = 0; i < THEMES.length; i++) {
			if (THEMES[i].id === id) return true;
		}
		return false;
	}

	function getStoredTheme() {
		try {
			return localStorage.getItem(STORAGE_KEY);
		} catch (e) {
			return null;
		}
	}

	function applyTheme(id, persist) {
		if (!isValidTheme(id)) id = DEFAULT_THEME;

		document.documentElement.setAttribute("data-theme", id);

		var meta = document.getElementById("theme-color-meta");
		if (meta) {
			for (var i = 0; i < THEMES.length; i++) {
				if (THEMES[i].id === id) {
					meta.setAttribute("content", THEMES[i].color);
					break;
				}
			}
		}

		if (persist !== false) {
			try {
				localStorage.setItem(STORAGE_KEY, id);
			} catch (e) {
				/* localStorage unavailable — theme just won't persist */
			}
		}

		if (document.dispatchEvent) {
			document.dispatchEvent(
				new CustomEvent("vibravid:theme-changed", { detail: { theme: id } })
			);
		}
	}

	// Apply immediately, before first paint. Don't persist — this is just
	// re-applying whatever was already stored (or the default).
	applyTheme(getStoredTheme() || DEFAULT_THEME, false);

	window.VibraVidTheme = {
		THEMES: THEMES,
		DEFAULT_THEME: DEFAULT_THEME,
		apply: applyTheme,
		current: function () {
			return document.documentElement.getAttribute("data-theme") || DEFAULT_THEME;
		},
	};
})();
