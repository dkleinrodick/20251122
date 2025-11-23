import psycopg2
import sys

USER = "postgres.vwtxmyboywwncglunrxv"
PASS = "egPI3VHuOf4QHSjv"
DB = "postgres"

def update_password_on_host(host, port, mode_name):
    print(f"\nConnecting to {host} ({mode_name})...")
    try:
        conn = psycopg2.connect(
            host=host,
            database=DB,
            user=USER,
            password=PASS,
            port=port,
            sslmode='require'
        )
        print("✅ Connected.")
        
        cur = conn.cursor()
        
        print("Updating admin_password...")
        sql = """
        INSERT INTO system_settings (key, value)
        VALUES ('admin_password', '84798479Aa!')
        ON CONFLICT (key) DO UPDATE
        SET value = EXCLUDED.value;
        """
        cur.execute(sql)
        conn.commit()
        print("✅ Password updated successfully to '84798479Aa!'.")
        
        cur.close()
        conn.close()
        return True
    except Exception as e:
        print(f"❌ FAILED on {host}: {e}")
        return False

if __name__ == "__main__":
    # Try aws-1 Transaction Mode (6543) - this worked in debug_db_local
    if not update_password_on_host("aws-1-us-east-1.pooler.supabase.com", 6543, "Transaction"):
        print("Retrying with Session Mode...")
        update_password_on_host("aws-1-us-east-1.pooler.supabase.com", 5432, "Session")
