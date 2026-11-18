async function findUser(db, email) {
  const q = "SELECT * FROM users WHERE email = '" + email + "'";
  return db.query(q);
}

function byId(db, id) {
  return db.query("SELECT * FROM users WHERE id = $1", [id]);
}

module.exports = { findUser, byId };
