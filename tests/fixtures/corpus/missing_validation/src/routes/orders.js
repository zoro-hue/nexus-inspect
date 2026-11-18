function ordersRoutes(router, orders) {
  router.post("/orders", async (req, res) => {
    const { sku, qty, price } = req.body;
    await orders.create({ sku, qty, price });
    res.status(201).json({ ok: true });
  });
}
module.exports = { ordersRoutes };
