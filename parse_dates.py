
import json
import re

raw_text = (
    "2025: January 1, 4-5, 16-17, 20; February 13-14, 17; March 14-16, 21-23, 28-30; April 4-6, 11-13, 18-21; May 22-23, 26; June 22, 26-29; July 3-7; August 28-29; September 1; October 9-10, 12-13; November 25, 26, 29-30; December 1, 20-23, 26-31.\n"
    "2026: January 1, 3-4, 15-16, 19; February 12-13, 16; March 13-15, 20-22, 27-29; April 3-6, 10-12; May 21-22, 25; June 25-28; July 2-6, September 3-4, 7; October 8-9, 11-12; November 24-25, 28-30; December 19-24, 26-31.\n"
    "2027: January 1-3, 14-15, 18; February 11-12, 15; March 12-14, 19-21, 26-29; April 2-4."
)

months = {
    "January": 1, "February": 2, "March": 3, "April": 4, "May": 5, "June": 6,
    "July": 7, "August": 8, "September": 9, "October": 10, "November": 11, "December": 12
}

results = []

for line in raw_text.strip().split('\n'):
    parts = line.split(':')
    if len(parts) < 2: continue
    year = int(parts[0].strip())
    data = parts[1].strip()
    
    # First split by semicolon to respect major groupings
    semicolon_chunks = re.split(r'[;.]', data)
    
    current_month = None
    
    for chunk in semicolon_chunks:
        chunk = chunk.strip()
        if not chunk: continue
        
        # Further split by comma to process individual items
        # "July 2-6, September 3-4, 7" -> ["July 2-6", " September 3-4", " 7"]
        
        # Special handling: "September 3-4, 7" vs "July 2-6"
        # We need to preserve order.
        
        items = chunk.split(',')
        
        for item in items:
            item = item.strip()
            if not item: continue
            
            # Check for month name
            found_new_month = False
            for m_name, m_num in months.items():
                if m_name in item:
                    current_month = m_num
                    item = item.replace(m_name, "").strip()
                    found_new_month = True
                    break
            
            if not current_month: continue
            
            # Clean item to get numbers/ranges
            item = item.replace(".", "")
            
            if '-' in item:
                # Range "4-5"
                try:
                    rng = item.split('-')
                    start = int(rng[0])
                    end = int(rng[1])
                    for d in range(start, end + 1):
                        results.append(f"{year}-{current_month:02d}-{d:02d}")
                except ValueError: pass
            else:
                # Single "1"
                try:
                    d = int(item)
                    results.append(f"{year}-{current_month:02d}-{d:02d}")
                except ValueError: pass

results = sorted(list(set(results))) # Unique and sorted
print(json.dumps(results))
