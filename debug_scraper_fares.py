import asyncio
import httpx
import json
import uuid
import random
import hashlib
import base64
from datetime import datetime
from typing import Dict, Any, List, Optional

class SimpleScraper:
    BASE_URL = "https://mtier.flyfrontier.com"
    
    def __init__(self):
        self.client = httpx.AsyncClient(verify=True, timeout=30.0)
        self.token = None
        self.headers = {}

    def _generate_uuid(self):
        u = uuid.uuid4()
        b = list(u.bytes)
        b[6] = (b[6] & 0x0f) | 0x40
        b[8] = (b[8] & 0x3f) | 0x80
        return uuid.UUID(bytes=bytes(b))

    def _generate_frontier_token(self):
        u = self._generate_uuid()
        prefix = random.randint(1, 9)
        return f"{prefix}{str(u).replace('-', '')}"

    def _generate_px_headers(self):
        u_uuid = str(self._generate_uuid())
        vid = str(self._generate_uuid())
        timestamp = int(datetime.now().timestamp() * 1000)
        hash_input = f"{u_uuid}{vid}{timestamp}"
        h = hashlib.md5(hash_input.encode()).hexdigest()
        px_auth_payload = json.dumps({"u": u_uuid, "v": vid, "t": timestamp, "h": h}, separators=(',', ':'))
        px_auth = "2:" + base64.b64encode(px_auth_payload.encode()).decode()
        device_fp = hashlib.md5(u_uuid.encode()).hexdigest()[:16]

        return {
            'x-px-vid': vid,
            'x-px-uuid': u_uuid,
            'x-px-os': 'Android',
            'x-px-os-version': '16',
            'x-px-device-model': 'sdk_gphone64_x86_64',
            'x-px-mobile-sdk-version': '3.4.5',
            'x-px-authorization': px_auth,
            'x-px-device-fp': device_fp,
            'x-px-hello': 'AgVSBgcKUFAeUAZQBB4CAlUDHgsBUAEeAFcFUVAABlJQClYK'
        }

    async def authenticate(self):
        print("Authenticating (Anonymous)...")
        initial_headers = {
            'ocp-apim-subscription-key': '493f95d2aa20409e9094b6ae78c1e5de',
            'frontiertoken': self._generate_frontier_token(),
            'user-agent': "NCPAndroid/3.3.0",
            'accept-encoding': 'gzip'
        }
        initial_headers.update(self._generate_px_headers())
        
        resp = await self.client.post(f"{self.BASE_URL}/registrationssv/RetrieveAnonymousToken", headers=initial_headers, json=None)
        if resp.status_code != 200:
            print(f"Auth Failed: {resp.text}")
            raise Exception(f"Authentication failed: {resp.status_code} - {resp.text}")
            
        data = resp.json()
        if 'data' not in data or 'authToken' not in data['data']:
            raise Exception(f"Invalid auth response: {data}")

        self.token = data['data']['authToken']
        self.headers = initial_headers.copy()
        self.headers['authtoken'] = self.token
        print("Authenticated!")

    def _get_fare_details(self, fares_map: Dict, fare_key: Optional[str], fare_type: str) -> Dict:
        details = {"fare_type": fare_type, "price": None, "seats_available": None, "fare_class": None}
        if not fare_key: return details

        fare_info = fares_map.get(fare_key)
        if fare_info and isinstance(fare_info, dict):
            details["price"] = fare_info.get('totals', {}).get('fareTotal')
            details["fare_class"] = fare_info.get('fareBasisCode')
            
            # Seats can sometimes be at different levels, often in the fare details list
            # Try to get it from fare.gowildFareDetails or similar from the original fare object
            # For this debug, we'll simplify and look in fare_info.fareDetails[0].availableCount if present
            if fare_info.get('fareDetails') and isinstance(fare_info['fareDetails'], list) and len(fare_info['fareDetails']) > 0:
                details["seats_available"] = fare_info['fareDetails'][0].get('availableCount')
        return details

    async def search(self, origin: str, dest: str, date: str):
        if not self.token: await self.authenticate()
        
        print(f"Searching {origin} -> {dest} on {date}...")
        payload = {
            "flightAvailabilityRequestModel": {
                "passengers": { "types": [{"type": "ADT", "count": 1}], "residentCountry": "US" },
                "filters": {
                    "maxConnections": 20,
                    "fareInclusionType": "Default", 
                    "type": "All"
                },
                "codes": { "currencyCode": "USD" },
                "origin": origin,
                "destination": dest,
                "beginDate": date
            }
        }
        self.headers.update(self._generate_px_headers())
        
        resp = await self.client.post(f"{self.BASE_URL}/flightavailabilityssv/FlightAvailabilitySimpleSearch", json=payload, headers=self.headers)
        resp.raise_for_status()
        data = resp.json()
        
        # Analyze Fares
        print(f"\n--- Detailed Fare Analysis for {origin}-{dest} on {date} ---")
        trips = data.get('data', {}).get('results', [])
        fares_map = data.get('data', {}).get('faresAvailable', {})

        if not trips: 
            print("No trips found in response.")
            return

        for trip in trips:
            for trip_data in trip.get('trips', []):
                journeys_dict = trip_data.get('journeysAvailableByMarket', {})
                for market_key, journeys in journeys_dict.items():
                    for journey in journeys:
                        print(f"  Journey: {market_key}")
                        segments_info = "; ".join([
                            f"{s.get('designator', {}).get('origin')}->{s.get('designator', {}).get('destination')}"
                            for s in journey.get('segments', [])
                        ])
                        print(f"    Segments: {segments_info}")

                        fares_list = journey.get('fares', [])
                        if not fares_list: 
                            print("      No fares available for this journey.")
                            continue
                        
                        # Assuming the first fare object contains all relevant keys
                        fare_object = fares_list[0] 

                        fare_keys = {
                            "standard": ("standardfareAvailabilityKey", "standardfaredetails"),
                            "discount_den": ("discountdenfareAvailabilityKey", "discountdenfaredetails"),
                            "gowild": ("gowildfareAvailabilityKey", "gowildfaredetails"),
                            "miles": ("milesfareAvailabilityKey", "milesfaredetails"),
                        }

                        for f_type, (key_field, details_field) in fare_keys.items():
                            key = fare_object.get(key_field)
                            
                            # Get Price & Class from Map
                            details = self._get_fare_details(fares_map, key, f_type)
                            
                            # Get Seats from Fare Object directly (The Fix)
                            raw_details = fare_object.get(details_field)
                            if raw_details and isinstance(raw_details, list) and len(raw_details) > 0:
                                details["seats_available"] = raw_details[0].get('availableCount')

                            if details["price"] is not None:
                                print(f"      - {details['fare_type'].replace('_', ' ').title()} Fare:")
                                print(f"          Price: ${details['price']:.2f}")
                                print(f"          Seats Available: {details['seats_available']}")
                                if details["fare_class"]:
                                    print(f"          Fare Class: {details['fare_class']}")
                                print(f"          Key: {key}")

        # Dump to file
        with open("Frontier_v2/scrape_debug.json", "w") as f:
            json.dump(data, f, indent=2)
        print("\nFull raw API response saved to Frontier_v2/scrape_debug.json")

    async def close(self):
        await self.client.aclose()

async def main():
    scraper = SimpleScraper()
    try:
        await scraper.search(origin="ORD", dest="DEN", date="2025-11-28")
    except Exception as e:
        print(f"Error during scrape: {e}")
    finally:
        await scraper.close()

if __name__ == "__main__":
    asyncio.run(main())
