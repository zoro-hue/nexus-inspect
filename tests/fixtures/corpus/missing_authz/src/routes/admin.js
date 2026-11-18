function adminRoutes(router, db, requireRole) {
  router.get("/api/admin/export", async (req, res) => {
    res.json(await db.all("SELECT * FROM audit_log"));
  });
  router.get("/api/admin/stats", requireRole("admin"), (req, res) => res.json({ ok: true }));
}

module.exports = { adminRoutes };
