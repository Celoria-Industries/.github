import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github/workflows/container-image.yml").read_text()


def step_script(name):
    step = WORKFLOW.split(f"      - name: {name}\n", 1)[1]
    script = step.split("        run: |\n", 1)[1].split("\n      - name:", 1)[0]
    return "\n".join(line[10:] for line in script.splitlines() if line.startswith("          "))


class ContainerWorkflowTest(unittest.TestCase):
    def setUp(self):
        self.env = os.environ.copy()
        self.env.update({
            "PUSH": "true",
            "IMAGE_NAME": "frontend-template",
            "CACHE_REPOSITORY": "frontend-template-cache",
            "AWS_REGION": "us-east-2",
            "ROLE_ARN": "arn:aws:iam::123456789012:role/push",
            "REQUESTED_COMMIT": "",
            "EVENT_NAME": "push",
            "REF": "refs/heads/main",
        })

    def run_step(self, name):
        return subprocess.run(
            ["bash", "-c", step_script(name)],
            env=self.env,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_publishing_requires_main_and_separate_cache(self):
        self.assertEqual(self.run_step("Validate publishing inputs").returncode, 0)
        for changes in (
            {"EVENT_NAME": "pull_request"},
            {"REF": "refs/heads/feature"},
            {"CACHE_REPOSITORY": "frontend-template"},
            {"ROLE_ARN": ""},
        ):
            with self.subTest(changes=changes):
                original = self.env.copy()
                self.env.update(changes)
                self.assertNotEqual(self.run_step("Validate publishing inputs").returncode, 0)
                self.env = original

    def test_publishing_can_skip_unprovisioned_cache(self):
        self.env["CACHE_REPOSITORY"] = ""
        self.assertEqual(self.run_step("Validate publishing inputs").returncode, 0)

    def test_pull_requests_build_without_aws(self):
        self.env.update({
            "PUSH": "false",
            "EVENT_NAME": "pull_request",
            "REF": "refs/pull/1/merge",
            "CACHE_REPOSITORY": "",
            "AWS_REGION": "",
            "ROLE_ARN": "",
        })
        self.assertEqual(self.run_step("Validate publishing inputs").returncode, 0)
        self.env["REQUESTED_COMMIT"] = "a" * 40
        self.assertNotEqual(self.run_step("Validate publishing inputs").returncode, 0)

    def test_build_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            podman = path / "podman"
            podman.write_text('#!/bin/bash\nprintf "%s\\0" "$@" > "$ARGS_FILE"\n')
            podman.chmod(0o755)
            self.env.update({
                "PATH": f"{directory}:{self.env['PATH']}",
                "ARGS_FILE": str(path / "args"),
                "CONTEXT": ".",
                "CONTAINERFILE": "Containerfile",
                "PLATFORMS": "linux/amd64",
                "IMAGE_COMMIT": "a" * 40,
                "ECR_REGISTRY": "123456789012.dkr.ecr.us-east-2.amazonaws.com",
                "GITHUB_SERVER_URL": "https://github.com",
                "GITHUB_REPOSITORY": "example/service",
            })
            cache = f"{self.env['ECR_REGISTRY']}/frontend-template-cache"
            node_image = f"{self.env['ECR_REGISTRY']}/docker-hub/library/node:26-slim"
            marker = path / "injected"
            hostile_image = f'{node_image}; $(touch "{marker}")\n--unexpected-flag'
            for push in ("true", "false"):
                for image in ("", node_image, hostile_image):
                    with self.subTest(push=push, node_image=image):
                        self.env.update({"PUSH": push, "NODE_IMAGE": image})
                        if push == "false":
                            self.env.pop("ECR_REGISTRY", None)
                        result = self.run_step("Build")
                        self.assertEqual(result.returncode, 0, result.stderr)
                        args = (path / "args").read_bytes().decode().split("\0")[:-1]
                        expected = [
                            "build",
                            "--platform", "linux/amd64",
                            "--manifest", f"localhost/celoria-image:{'a' * 40}",
                            "--label", "org.opencontainers.image.source=https://github.com/example/service",
                            "--label", f"org.opencontainers.image.revision={'a' * 40}",
                        ]
                        if push == "true":
                            expected.extend(["--layers", "--cache-from", cache, "--cache-to", cache])
                            if image:
                                expected.extend(["--build-arg", f"NODE_IMAGE={image}"])
                        expected.extend(["-f", "./Containerfile", "."])
                        self.assertEqual(args, expected)
                        self.assertFalse(marker.exists())

    def test_push_outputs_the_ecr_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            executable = path / "podman"
            executable.write_text(
                "#!/bin/bash\n"
                "while [[ $# -gt 0 ]]; do\n"
                "  if [[ \"$1\" == \"--digestfile\" ]]; then\n"
                "    printf '%s' \"$EXPECTED_DIGEST\" > \"$2\"\n"
                "    shift 2\n"
                "  else\n"
                "    shift\n"
                "  fi\n"
                "done\n"
            )
            executable.chmod(0o755)
            digest = "sha256:" + "a" * 64
            output = path / "output"
            summary = path / "summary"
            self.env.update({
                "PATH": f"{directory}:{self.env['PATH']}",
                "IMAGE_NAME": "frontend-template",
                "AWS_REGION": "us-east-2",
                "IMAGE_COMMIT": "b" * 40,
                "ECR_REGISTRY": "123456789012.dkr.ecr.us-east-2.amazonaws.com",
                "EXPECTED_DIGEST": digest,
                "RUNNER_TEMP": directory,
                "GITHUB_OUTPUT": str(output),
                "GITHUB_STEP_SUMMARY": str(summary),
            })
            result = self.run_step("Push image to ECR")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(output.read_text(), f"image_digest={digest}\n")
            self.assertIn(digest, summary.read_text())


if __name__ == "__main__":
    unittest.main()
