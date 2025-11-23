import psycopg2
import sys

# Configuration provided by user
HOST = "aws-1-us-east-1.pooler.supabase.com"
USER = "postgres.vwtxmyboywwncglunrxv"
PASS = "egPI3VHuOf4QHSjv"
DB = "postgres"

def test_conn(port, mode):
    print(f"\nTesting {mode} (Port {port})...")
    try:
        conn = psycopg2.connect(
            host=HOST,
            database=DB,
            user=USER,
            password=PASS,
            port=port,
            sslmode='require'
        )
        print("✅ SUCCESS! Connected.")
        cur = conn.cursor()
        cur.execute("SELECT version();")
        print(f"Version: {cur.fetchone()[0]}")
        conn.close()
        return True
    except Exception as e:
        print(f"❌ FAILED: {e}")
        return False

if __name__ == "__main__":
    # Test Session Mode
    s_success = test_conn(5432, "Session Mode")
    
    # Test Transaction Mode
    t_success = test_conn(6543, "Transaction Mode")
    
    if not s_success and not t_success:
        print("\nBoth failed. Checking host aws-0 as fallback...")
        HOST = "aws-0-us-east-1.pooler.supabase.com"
        # Re-define function to use new HOST or just pass it manually
        try:
            conn = psycopg2.connect(
                host=HOST,
                database=DB,
                user=USER,
                password=PASS,
                port=5432,
                sslmode='require'
            )
            print("✅ SUCCESS! Connected to AWS-0.")
            conn.close()
        except Exception as e:
            print(f"❌ FAILED AWS-0: {e}")
