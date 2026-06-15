/**
 * Onhas Yapı CMS — Ana JavaScript
 * CSP uyumlu (harici dosya, inline yok)
 */

// === Navbar Scroll Efekti ===
window.addEventListener('scroll', function() {
    const nav = document.getElementById('mainNav');
    if (!nav) return;
    if (window.scrollY > 50) {
        nav.classList.add('nav-scrolled');
    } else {
        nav.classList.remove('nav-scrolled');
    }
});

// === TEMA YÖNETİMİ ===
(function() {
    const THEME_KEY = 'onhas-theme';
    
    function getTheme() {
        var saved = localStorage.getItem(THEME_KEY);
        if (saved) return saved;
        return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark';
    }
    
    function setTheme(theme) {
        document.body.setAttribute('data-theme', theme);
        localStorage.setItem(THEME_KEY, theme);
        var icon = document.getElementById('themeIcon');
        if (icon) {
            icon.className = theme === 'light' ? 'bi bi-moon-fill' : 'bi bi-sun-fill';
        }
        var meta = document.querySelector('meta[name="theme-color"]');
        if (meta) meta.content = theme === 'light' ? '#f5f0e8' : '#1a1a2e';
    }
    
    // Başlangıç
    setTheme(getTheme());
    
    // Toggle fonksiyonu (butondan çağrılır)
    window.toggleTheme = function() {
        var current = document.body.getAttribute('data-theme');
        setTheme(current === 'light' ? 'dark' : 'light');
    };
})();
