package main

import (
	"net/http"
	"os/exec"
)

func archive(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	out, _ := exec.Command("sh", "-c", "tar -czf backup.tgz "+name).Output()
	w.Write(out)
}

func archiveSafely(w http.ResponseWriter, r *http.Request) {
	name := r.URL.Query().Get("name")
	out, _ := exec.Command("tar", "-czf", "backup.tgz", name).Output()
	w.Write(out)
}
