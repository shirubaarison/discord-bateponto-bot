(() => {
    const storageKey = "bate-ponto-theme";

    const preferredTheme = () => {
        try {
            const savedTheme = window.localStorage.getItem(storageKey);
            if (savedTheme === "light" || savedTheme === "dark") return savedTheme;
        } catch (_) {
            // Use the system preference when storage is unavailable.
        }
        return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    };

    const applyTheme = (theme) => {
        document.documentElement.dataset.theme = theme;
        const darkTheme = theme === "dark";
        const actionLabel = darkTheme ? "Ativar tema claro" : "Ativar tema escuro";

        document.querySelectorAll("[data-theme-toggle]").forEach(button => {
            button.setAttribute("aria-label", actionLabel);
            button.setAttribute("title", actionLabel);
        });
        document.querySelectorAll("[data-theme-label]").forEach(label => {
            label.textContent = actionLabel;
        });
        document.querySelectorAll("[data-theme-icon]").forEach(icon => {
            icon.setAttribute("data-lucide", darkTheme ? "sun" : "moon");
        });

        if (window.lucide) window.lucide.createIcons();
    };

    const initialTheme = preferredTheme();
    document.documentElement.dataset.theme = initialTheme;

    const setupThemeControls = () => {
        applyTheme(document.documentElement.dataset.theme || initialTheme);
        document.querySelectorAll("[data-theme-toggle]").forEach(button => {
            button.addEventListener("click", () => {
                const nextTheme = document.documentElement.dataset.theme === "dark"
                    ? "light"
                    : "dark";
                try {
                    window.localStorage.setItem(storageKey, nextTheme);
                } catch (_) {
                    // The selected theme still applies for the current page.
                }
                applyTheme(nextTheme);
            });
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", setupThemeControls);
    } else {
        setupThemeControls();
    }
})();
