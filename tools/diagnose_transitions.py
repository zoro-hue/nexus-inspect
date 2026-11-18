import time
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

def run_diagnostics():
    print("Running Homepage Section Transitions Diagnostics...")
    opts = Options()
    opts.add_argument("--headless=new")
    opts.add_argument("--disable-gpu")
    opts.add_argument("--window-size=1440,900")
    # First check default OS setting
    driver_default = webdriver.Chrome(options=opts)
    driver_default.get("http://127.0.0.1:8000/#home")
    time.sleep(0.5)
    default_reduced = driver_default.execute_script("return window.matchMedia('(prefers-reduced-motion: reduce)').matches;")
    driver_default.quit()
    print(f"Host OS default prefers-reduced-motion: {default_reduced}")

    # Now run diagnostic with emulated no-preference (normal motion)
    opts.set_capability("goog:loggingPrefs", {"browser": "ALL"})
    d = webdriver.Chrome(options=opts)
    d.execute_cdp_cmd("Emulation.setEmulatedMedia", {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]})
    
    sections = [
        ("Hero", "#home"),
        ("Statement", "#statement"),
        ("Capabilities", "#capabilities"),
        ("How It Works", "#how"),
        ("Evidence", "#evidence"),
        ("Investigation Graph", "#investigation"),
        ("Exports", "#exports"),
        ("Limits", "#limits"),
        ("Final CTA", "#about")
    ]
    
    try:
        d.get("http://127.0.0.1:8000/#home")
        time.sleep(1.0)
        
        print("\n--- SECTION TRANSITIONS OBSERVATION (1440px) ---")
        for name, sel in sections:
            el = d.find_element(By.CSS_SELECTOR, sel)
            # Check if section itself or children have reveal classes
            section_classes = el.get_attribute("class")
            reveals = el.find_elements(By.CSS_SELECTOR, ".hm-rv, .reveal, .js-reveal")
            title = el.find_elements(By.CSS_SELECTOR, "h1, h2")
            title_text = title[0].text if title else "(no title)"
            title_classes = title[0].get_attribute("class") if title else ""
            title_opacity = title[0].value_of_css_property("opacity") if title else ""
            title_transform = title[0].value_of_css_property("transform") if title else ""
            
            print(f"Section '{name}' ({sel}):")
            print(f"  Title: '{title_text}', classes='{title_classes}', opacity={title_opacity}, transform={title_transform}")
            print(f"  Section classes='{section_classes}', inner reveals count={len(reveals)}")
            
        print("\n--- IDLE ANIMATIONS & RAF MEASUREMENT ---")
        # Check idle animations
        anims = d.execute_script("return document.getAnimations ? document.getAnimations().filter(a => a.playState === 'running').map(a => a.animationName || '(transition)') : [];")
        print(f"Active running animations at idle: {anims} (count={len(anims)})")
        
        # Check rAF execution while idle
        d.execute_script("""
            window.__raf_count = 0;
            const origRaf = window.requestAnimationFrame;
            window.requestAnimationFrame = function(cb) {
                window.__raf_count++;
                return origRaf(cb);
            };
        """)
        time.sleep(1.5)
        raf_calls = d.execute_script("return window.__raf_count;")
        print(f"rAF callbacks fired over 1.5s idle: {raf_calls}")
        
    finally:
        d.quit()

if __name__ == "__main__":
    run_diagnostics()
