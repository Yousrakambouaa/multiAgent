"""Run mocked unit tests independently of personal model-routing preferences."""
import os
import unittest


def main():
    os.environ.update(STUDIO_MODEL_SELECTION="dynamic", STUDIO_MODEL_FALLBACK_SCOPE="catalog",
                      STUDIO_PAID_ONLY="0", STUDIO_REASONING_EFFORT="low")
    suite = unittest.defaultTestLoader.discover("tests")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
