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
            {"CACHE_REPOSITORY": ""},
            {"ROLE_ARN": ""},
        ):
            with self.subTest(changes=changes):
                original = self.env.copy()
                self.env.update(changes)
                self.assertNotEqual(self.run_step("Validate publishing inputs").returncode, 0)
                self.env = original

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

    def test_build_cache_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            podman = path / "podman"
            podman.write_text('#!/bin/bash\nprintf "%s\\n" "$@" > "$ARGS_FILE"\n')
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
            self.assertEqual(self.run_step("Build").returncode, 0)
            args = (path / "args").read_text().splitlines()
            cache = f"{self.env['ECR_REGISTRY']}/frontend-template-cache"
            self.assertIn("--layers", args)
            self.assertEqual(args[args.index("--cache-from") + 1], cache)
            self.assertEqual(args[args.index("--cache-to") + 1], cache)

            self.env["PUSH"] = "false"
            self.assertEqual(self.run_step("Build").returncode, 0)
            args = (path / "args").read_text().splitlines()
            self.assertNotIn("--cache-from", args)
            self.assertNotIn("--cache-to", args)


if __name__ == "__main__":
    unittest.main()
