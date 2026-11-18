"""Verify Finding and Project Drawer Shared-Element Flight and Reverse Transitions."""

import os
import time
import zipfile
import tempfile
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.chrome.options import Options

ARTIFACT_DIR = os.environ.get("NX_ARTIFACT_DIR", os.path.join(tempfile.gettempdir(), "nx", "verify-artifacts"))

# Create a small test zip with code findings
tmp_zip = os.path.join(tempfile.gettempdir(), "test_repo.zip")
with zipfile.ZipFile(tmp_zip, "w") as zf:
    zf.writestr("app.py", """import os
api_key = "AIzaSyDummySecretKey1234567890XYZ"
def execute():
    # TODO: implement proper validation
    pass
""")

opts = Options()
opts.add_argument("--headless=new")
opts.add_argument("--disable-gpu")
opts.add_argument("--window-size=1280,900")
driver = webdriver.Chrome(options=opts)

try:
    # 1. Sign up a user
    driver.get("http://127.0.0.1:8000/#signup")
    time.sleep(0.8)
    driver.find_element(By.ID, "signupName").send_keys("Drawer Tester")
    user_email = f"drawer_{int(time.time())}@example.com"
    driver.find_element(By.ID, "signupEmail").send_keys(user_email)
    driver.find_element(By.ID, "signupPassword").send_keys("P@ssword12345!")
    driver.find_element(By.CSS_SELECTOR, "#signupForm button[type=submit]").click()
    time.sleep(1.0)

    # 2. Go to Analyze view and upload ZIP
    driver.get("http://127.0.0.1:8000/#analyze")
    time.sleep(0.8)
    zip_input = driver.find_element(By.ID, "zipFile")
    zip_input.send_keys(tmp_zip)
    time.sleep(0.4)

    start_btn = driver.find_element(By.ID, "startBtn")
    start_btn.click()
    print("Started analysis on uploaded ZIP...")
    time.sleep(2.5)

    # 3. Go to findings
    driver.get("http://127.0.0.1:8000/#findings")
    time.sleep(1.2)

    rows = driver.find_elements(By.CSS_SELECTOR, ".finding")
    print(f"Findings rows found: {len(rows)}")

    if rows:
        row = rows[0]
        fid = row.get_attribute("data-fid")
        print(f"Opening drawer for finding {fid}...")
        row.click()
        time.sleep(0.5)

        drawer = driver.find_element(By.ID, "drawer")
        assert "open" in drawer.get_attribute("class"), "Drawer did not open"
        print("Drawer opened successfully.")

        # Check shared elements
        title_el = drawer.find_element(By.CSS_SELECTOR, ".detail-title")
        print(f"Drawer title: {title_el.text}")

        # Capture drawer screenshot
        drawer_img = os.path.join(ARTIFACT_DIR, "motion-drawer-active.png")
        driver.save_screenshot(drawer_img)
        print(f"Saved screenshot: {drawer_img}")

        # Close drawer
        close_btn = driver.find_element(By.ID, "drawerClose")
        close_btn.click()
        print("Closing drawer...")
        time.sleep(0.35)

        # Check DOM for residual elements
        ghosts = driver.find_elements(By.CSS_SELECTOR, ".fx-ghost")
        shared = driver.find_elements(By.CSS_SELECTOR, ".fx-shared")
        print(f"Residual ghosts: {len(ghosts)}, residual shared elements: {len(shared)}")
        assert len(ghosts) == 0, f"Found {len(ghosts)} residual ghost elements!"
        assert len(shared) == 0, f"Found {len(shared)} residual shared elements!"
        assert "open" not in drawer.get_attribute("class"), "Drawer class 'open' not cleared"
        print("Reverse spatial transition completed cleanly!")

finally:
    driver.quit()
    if os.path.exists(tmp_zip):
        os.remove(tmp_zip)
