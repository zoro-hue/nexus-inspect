const crypto = require("crypto");

function sessionToken() {
  return crypto.randomBytes(16).toString("hex");
}

module.exports = { sessionToken };
