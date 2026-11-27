"""Voroa entry point: one central process runs all three market strategies."""
from central_worker import *  # re-export scanner settings and helpers for operators/tests
from central_worker import main


if __name__ == "__main__":
    main()
