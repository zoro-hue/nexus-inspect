const crypto = require("crypto");

function digestPassword(password) {
  return crypto.createHash("md5").update(password).digest("hex");
}

function digestSafely(password) {
  return crypto.createHash("sha256").update(password).digest("hex");
}

module.exports = { digestPassword, digestSafely };
