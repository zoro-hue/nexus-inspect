import os
import time
import json
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

def main():
    os.makedirs("screenshots", exist_ok=True)
    chrome_options = Options()
    chrome_options.add_argument("--headless=new")
    chrome_options.add_argument("--window-size=1440,900")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--no-sandbox")

    driver = webdriver.Chrome(options=chrome_options)
    try:
        # Set reduced motion to no-preference
        driver.execute_cdp_cmd(
            "Emulation.setEmulatedMedia",
            {"features": [{"name": "prefers-reduced-motion", "value": "no-preference"}]}
        )

        driver.get("http://127.0.0.1:8000/#home")
        time.sleep(1)

        # 1. Measure frame durations during a full smooth scroll
        frame_perf_script = """
        return new Promise((resolve) => {
            const frameDeltas = [];
            let lastTime = performance.now();
            let scrolling = true;

            function onFrame(now) {
                const delta = now - lastTime;
                lastTime = now;
                frameDeltas.push(delta);
                if (scrolling) {
                    requestAnimationFrame(onFrame);
                } else {
                    resolve(frameDeltas);
                }
            }
            requestAnimationFrame(onFrame);

            // Scroll down gradually
            let pos = 0;
            const maxScroll = document.documentElement.scrollHeight - window.innerHeight;
            const interval = setInterval(() => {
                pos += 120;
                window.scrollTo(0, pos);
                if (pos >= maxScroll) {
                    clearInterval(interval);
                    setTimeout(() => {
                        scrolling = false;
                    }, 500);
                }
            }, 30);
        });
        """
        print("Measuring scroll frame timing...")
        frame_deltas = driver.execute_script(frame_perf_script)
        # Drop the first frame delta as it measures initialization
        if len(frame_deltas) > 1:
            deltas = frame_deltas[1:]
        else:
            deltas = frame_deltas

        longest_frame = max(deltas) if deltas else 0
        avg_frame = sum(deltas) / len(deltas) if deltas else 0
        frames_over_16_7 = sum(1 for d in deltas if d > 16.7)
        frames_over_33 = sum(1 for d in deltas if d > 33.3)
        total_frames = len(deltas)

        perf_results = {
            "total_frames": total_frames,
            "longest_frame_ms": round(longest_frame, 2),
            "avg_frame_ms": round(avg_frame, 2),
            "frames_over_16_7ms": frames_over_16_7,
            "pct_over_16_7ms": round(frames_over_16_7 / total_frames * 100, 1) if total_frames else 0,
            "frames_over_33_3ms": frames_over_33
        }
        print("Performance Results:", json.dumps(perf_results, indent=2))

        # 2. Capture a transition frame sequence of a section entering
        # Refresh and scroll to just above #statement
        driver.get("http://127.0.0.1:8000/#home")
        time.sleep(1)
        driver.execute_script("window.scrollTo(0, 0);")
        time.sleep(0.5)

        # Trigger scroll down to #statement and capture frames
        print("Capturing transition frame sequence for #statement...")
        # Get target position for #statement
        stmt_pos = driver.execute_script("""
            const el = document.getElementById('statement');
            return el.getBoundingClientRect().top + window.scrollY - 300;
        """)
        driver.execute_script(f"window.scrollTo(0, {stmt_pos});")

        frame_paths = []
        for i in range(8):
            path = f"screenshots/transition-frame-{i+1}.png"
            driver.save_screenshot(path)
            frame_paths.append(path)
            time.sleep(0.12) # ~120ms intervals across the 900ms transition

        print(f"Captured {len(frame_paths)} transition frames.")

        with open("screenshots/frame_timing.json", "w") as f:
            json.dump(perf_results, f, indent=2)

    finally:
        driver.quit()

if __name__ == "__main__":
    main()
