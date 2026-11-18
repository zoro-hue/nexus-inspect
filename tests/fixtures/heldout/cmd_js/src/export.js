const { exec, execFile } = require("child_process");

function archive(file) {
  exec(`tar -czf backup.tgz ${file}`, (err) => {
    if (err) console.error(err.message);
  });
}

function archiveSafely(file) {
  execFile("tar", ["-czf", "backup.tgz", file], (err) => {
    if (err) console.error(err.message);
  });
}

module.exports = { archive, archiveSafely };
