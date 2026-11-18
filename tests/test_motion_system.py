"""Unit and integration tests for NEXUS INSPECT Motion System Refinement."""

import unittest
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

class TestMotionSystemAudit(unittest.TestCase):
    def read(self, relpath):
        with open(os.path.join(ROOT, relpath), "r", encoding="utf-8") as f:
            return f.read()

    def test_styles_css_tokens_and_easings(self):
        css = self.read("styles.css")
        self.assertIn("--ease-apple", css)
        self.assertIn("--ease-spring", css)
        self.assertIn("--ease-out-fast", css)
        self.assertIn("--ease-settle", css)
        # Verify no conflicting viewIn on .view.active
        self.assertNotIn("animation:viewIn", css)
        # Verify drawer has both transform and opacity transitions
        self.assertIn("transition:transform .28s var(--ease-spring),opacity .22s var(--ease-apple)", css)
        self.assertIn("transition:transform .32s var(--ease-spring),opacity .24s var(--ease-apple)", css)
        # Verify .capsule-ind uses var(--ease-apple)
        self.assertIn("transition:transform .24s var(--ease-apple),width .24s var(--ease-apple)", css)

    def test_dash_css_refinements(self):
        css = self.read("dash.css")
        # Verify capsule nav indicator glides with Apple easing curve
        self.assertIn("#capsuleInd{position:absolute;transition:transform 240ms cubic-bezier(.16,1,.3,1),width 240ms cubic-bezier(.16,1,.3,1)}", css)
        # Verify drawer closing rule exists for spatial reverse transition
        self.assertIn(".drawer.closing{opacity:0;transform:translateX(100%);pointer-events:none}", css)
        # Verify island transitions
        self.assertIn("transition:width .28s cubic-bezier(.16,1,.3,1)", css)
        # Verify reduced motion rules
        self.assertIn("@media (prefers-reduced-motion:reduce)", css)

    def test_dash_js_drawer_and_navigation(self):
        js = self.read("dash.js")
        # Verify cleanFx helper exists
        self.assertIn("function cleanFx()", js)
        # Verify closeDrawer does NOT overwrite transition with inline opacity only
        self.assertNotIn("drawer.style.transition = 'opacity .28s", js)
        # Verify closeDrawer sets transform and opacity transition
        self.assertIn("drawer.style.transition = 'transform .28s cubic-bezier(.2,.9,.25,1), opacity .22s cubic-bezier(.16,1,.3,1)'", js)
        # Verify moveIndicator supports immediate targetView parameter
        self.assertIn("function moveIndicator(targetView = state.view)", js)
        # Verify showView glides indicator immediately
        self.assertIn("moveIndicator(name);", js)
        # Verify stale switch cancellation in showView
        self.assertIn("if (pendingSwitch.anim) pendingSwitch.anim.cancel();", js)

    def test_app_js_mega_menu_morphing(self):
        js = self.read("app.js")
        # Verify morphing logic exists for alreadyOpen
        # Sequential handoff: outgoing exits alone, then the incoming panel reveals (no overlap)
        self.assertIn("const wasOpen = prevName !== null && prevName !== name;", js)
        self.assertIn("switchTimer = setTimeout(() => {", js)
        self.assertIn("finalizeExit();", js)
        # Verify directional translation calculation
        self.assertIn("const dir = newIdx >= oldIdx ? 1 : -1;", js)
        # Verify smooth height morphing
        self.assertIn("mega.style.height = targetH + 'px';", js)
        # Verify hover intent delay
        self.assertIn("hoverTimer = setTimeout(() => openMega(t.dataset.menu), 110);", js)
        # Verify grace period on pointer leave
        self.assertIn("closeTimer = setTimeout(closeMega, 200);", js)
        # Verify reduced motion check
        self.assertIn("isReducedMotion", js)

    def test_docs_css_clean_transitions(self):
        css = self.read("docs.css")
        # Verify double title animation was removed
        self.assertNotIn(".doc-in .doc-title { animation: docIn", css)
        # Verify clean single docIn animation
        self.assertIn(".doc-in { animation: docIn .24s cubic-bezier(.16,1,.3,1) both; }", css)

if __name__ == "__main__":
    unittest.main()
