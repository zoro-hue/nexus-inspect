async function charge(order) {
  // TODO: integrate the payment provider
  return { status: "succeeded" };
}

async function refund(order) {
  try {
    const res = await fetch("https://api.provider.example/refund", { method: "POST" });
    return { status: res.ok ? "refunded" : "failed" };
  } catch (err) {
    throw err;
  }
}

module.exports = { charge, refund };
