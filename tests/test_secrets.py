import unittest

from askgpt.secrets import format_findings, scan


class ScanTest(unittest.TestCase):
    def test_detects_openai_key(self):
        findings = scan("token = sk-abcdefghij0123456789ABCD")
        self.assertEqual([f.name for f in findings], ["openai-key"])

    def test_detects_github_pat(self):
        findings = scan("ghp_0123456789abcdefghijABCDEFGHIJ0123")
        self.assertEqual([f.name for f in findings], ["github-pat"])

    def test_detects_aws_access_key(self):
        self.assertTrue(scan("AKIAIOSFODNN7EXAMPLE"))

    def test_detects_bearer_token(self):
        self.assertTrue(scan("Authorization: Bearer abcdefghijklmnopqrstuvwxyz012345"))

    def test_detects_private_key_block(self):
        self.assertTrue(scan("-----BEGIN RSA PRIVATE KEY-----"))

    def test_reports_line_numbers(self):
        findings = scan("clean\nclean\nsk-abcdefghij0123456789ABCD")
        self.assertEqual(findings[0].line, 3)

    def test_excerpt_is_redacted(self):
        secret = "sk-abcdefghij0123456789ABCD"
        finding = scan(secret)[0]
        self.assertNotIn(secret, finding.excerpt)
        self.assertIn("...", finding.excerpt)

    def test_detects_slack_token(self):
        findings = scan("xoxb-1234567890-abcdefghijkl")
        self.assertEqual([f.name for f in findings], ["slack-token"])

    def test_reports_every_finding_not_just_the_first(self):
        # Catches an implementation that returns on first match.
        findings = scan("sk-abcdefghij0123456789ABCD\nAKIAIOSFODNN7EXAMPLE")
        self.assertEqual(len(findings), 2)

    def test_format_findings_never_prints_the_secret(self):
        # format_findings is what the user actually sees. Untested, it could
        # leak the very secret it is warning about.
        secret = "sk-abcdefghij0123456789ABCD"
        message = format_findings(scan(secret))
        self.assertNotIn(secret, message)
        self.assertIn("line 1", message)
        self.assertIn("openai-key", message)
        self.assertIn("--allow-secrets", message)

    def test_clean_text_yields_nothing(self):
        self.assertEqual(scan("def add(a, b):\n    return a + b\n"), [])

    def test_prose_mentioning_sk_is_not_flagged(self):
        self.assertEqual(scan("the sk- prefix identifies OpenAI keys"), [])


if __name__ == "__main__":
    unittest.main()
