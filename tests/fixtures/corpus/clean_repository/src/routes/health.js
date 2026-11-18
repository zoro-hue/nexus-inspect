function health(req, res) {
  try {
    res.json({ ok: true });
  } catch (err) {
    res.status(500).json({ error: "internal" });
  }
}
module.exports = { health };
