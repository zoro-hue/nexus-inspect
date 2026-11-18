function showComment(req, el) {
  el.innerHTML = req.query.message;
}

function showCommentSafely(message, el) {
  el.textContent = message;
}

module.exports = { showComment, showCommentSafely };
