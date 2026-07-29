import subprocess
import sys

def run_graphify():
    try:
        result = subprocess.run(
            [sys.executable, "-m", "graphify", "."],
            capture_output=True,
            text=True,
            cwd="e:\\AI\\GitHub\\RagFlow",
            timeout=300
        )
        print("STDOUT:", result.stdout)
        print("STDERR:", result.stderr)
        print("Return code:", result.returncode)
    except subprocess.TimeoutExpired:
        print("Timeout!")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    run_graphify()