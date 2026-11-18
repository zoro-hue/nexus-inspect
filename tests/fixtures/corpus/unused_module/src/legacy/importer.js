function importLegacyCsv(path) {
  return require("fs").readFileSync(path, "utf8");
}
module.exports = { importLegacyCsv };
