package tools

import "os/exec"

func Pack(dir string) ([]byte, error) {
	return exec.Command("tar", "-czf", "backup.tgz", dir).Output()
}
