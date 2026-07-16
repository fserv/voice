"""Thin Sabre Dev Studio (CERT) REST client for the voice travel agent.

Implements the three operations the voice agent needs:
  * OAuth2 client-credentials token (cached until expiry)
  * flight search  -> Bargain Finder Max (OTA_AirLowFareSearchRQ v4.3.0)
  * booking        -> Create Passenger Name Record (CreatePassengerNameRecordRQ v2.4.0)

Everything is normalised into small, voice-friendly dicts so the agent can read
results aloud without wading through Sabre's verbose OTA payloads.

Set SABRE_MOCK=1 to return canned data (no network / no creds required) so the
whole stack is demoable before real credentials are wired in.
"""
from __future__ import annotations

import base64
import os
import time
import uuid
from typing import Any

import requests


class SabreError(RuntimeError):
    """Raised when Sabre returns an error we cannot recover from."""


def _mock_enabled() -> bool:
    return os.environ.get("SABRE_MOCK", "0") == "1"


# ---- airline metadata / imagery (used to enrich results for the browser UI) ----
AIRLINES = {
    "AA": "American Airlines", "DL": "Delta Air Lines", "UA": "United Airlines",
    "B6": "JetBlue", "AS": "Alaska Airlines", "WN": "Southwest Airlines",
    "NK": "Spirit Airlines", "F9": "Frontier Airlines", "HA": "Hawaiian Airlines",
    "G4": "Allegiant Air", "AC": "Air Canada", "BA": "British Airways",
    "LH": "Lufthansa", "AF": "Air France", "KL": "KLM", "EK": "Emirates",
    "QF": "Qantas", "SQ": "Singapore Airlines", "VS": "Virgin Atlantic",
}


def airline_name(code: str) -> str:
    return AIRLINES.get((code or "").upper(), code or "Unknown")


def airline_logo(code: str) -> str:
    """Airline logo PNG by IATA code (free Kiwi.com image CDN)."""
    return f"https://images.kiwi.com/airlines/128/{(code or '').upper()}.png"


def airline_photo(code: str) -> str:
    """An airplane photo flavored to the airline (free LoremFlickr, no key)."""
    tags = ",".join(["airplane"] + airline_name(code).split())
    return f"https://loremflickr.com/640/360/{tags}"


# ---- hotel chain / car vendor metadata + imagery (mirrors the airline helpers) ----
HOTEL_CHAINS = {
    "HL": "Hilton", "MC": "Marriott", "HY": "Hyatt", "IC": "InterContinental",
    "BW": "Best Western", "HI": "Holiday Inn", "SI": "Sheraton", "WI": "Westin",
    "RD": "Radisson", "FS": "Four Seasons", "CP": "Crowne Plaza", "WY": "Wyndham",
}

CAR_VENDORS = {
    "ZE": "Hertz", "ZI": "Avis", "ZD": "Budget", "ET": "Enterprise",
    "ZR": "National", "AL": "Alamo", "ZT": "Thrifty", "SX": "Sixt", "ZL": "Dollar",
}


def hotel_chain_name(code: str) -> str:
    return HOTEL_CHAINS.get((code or "").upper(), code or "")


def hotel_photo(name: str) -> str:
    """A hotel photo flavored to the property name (free LoremFlickr, no key)."""
    tags = ",".join(["hotel"] + (name or "").split()[:2])
    return f"https://loremflickr.com/640/360/{tags}"


def car_vendor_name(code: str) -> str:
    return CAR_VENDORS.get((code or "").upper(), code or "")


def car_photo(car_type: str) -> str:
    """A car photo flavored to the vehicle class (free LoremFlickr, no key)."""
    tags = ",".join(["car"] + (car_type or "car").split())
    return f"https://loremflickr.com/640/360/{tags}"


def _span_days(start: str, end: str, floor: int = 1) -> int:
    """Whole days between two YYYY-MM-DD strings (>= floor)."""
    from datetime import date

    try:
        d1 = date.fromisoformat(start)
        d2 = date.fromisoformat(end)
        return max((d2 - d1).days, floor)
    except (ValueError, TypeError):
        return floor


class SabreClient:
    def __init__(
        self,
        base_url: str | None = None,
        client_id: str | None = None,
        client_secret: str | None = None,
        pcc: str | None = None,
        timeout: int = 20,
    ):
        self.base_url = (base_url or os.environ.get("SABRE_BASE_URL", "https://api.cert.platform.sabre.com")).rstrip("/")
        self.client_id = client_id or os.environ.get("SABRE_CLIENT_ID", "")
        self.client_secret = client_secret or os.environ.get("SABRE_CLIENT_SECRET", "")
        self.pcc = pcc or os.environ.get("SABRE_PCC", "S5OM")
        self.timeout = timeout
        self._token: str | None = None
        self._token_exp: float = 0.0

    # ---------------------------------------------------------------- auth
    def _access_token(self) -> str:
        """Return a cached bearer token, refreshing ~60s before expiry."""
        if self._token and time.time() < self._token_exp - 60:
            return self._token

        # Sabre credential = base64( base64(client_id) + ":" + base64(client_secret) )
        cid = base64.b64encode(self.client_id.encode()).decode()
        sec = base64.b64encode(self.client_secret.encode()).decode()
        cred = base64.b64encode(f"{cid}:{sec}".encode()).decode()

        resp = requests.post(
            f"{self.base_url}/v2/auth/token",
            headers={
                "Authorization": f"Basic {cred}",
                "Content-Type": "application/x-www-form-urlencoded",
            },
            data={"grant_type": "client_credentials"},
            timeout=self.timeout,
        )
        if resp.status_code != 200:
            raise SabreError(f"Sabre auth failed ({resp.status_code}): {resp.text[:300]}")
        data = resp.json()
        self._token = data["access_token"]
        self._token_exp = time.time() + int(data.get("expires_in", 604800))
        return self._token

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token()}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    # -------------------------------------------------------------- search
    def search_flights(
        self,
        origin: str,
        destination: str,
        departure_date: str,
        return_date: str | None = None,
        adults: int = 1,
        cabin: str | None = None,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search flights via Bargain Finder Max. Returns a list of normalised offers."""
        if _mock_enabled():
            return _mock_offers(origin, destination, departure_date, return_date, max_results)

        origin, destination = origin.upper(), destination.upper()
        od = [
            {
                "RPH": "1",
                "DepartureDateTime": f"{departure_date}T00:00:00",
                "OriginLocation": {"LocationCode": origin},
                "DestinationLocation": {"LocationCode": destination},
            }
        ]
        if return_date:
            od.append(
                {
                    "RPH": "2",
                    "DepartureDateTime": f"{return_date}T00:00:00",
                    "OriginLocation": {"LocationCode": destination},
                    "DestinationLocation": {"LocationCode": origin},
                }
            )

        travel_prefs: dict[str, Any] = {"TPA_Extensions": {"NumTrips": {"Number": max_results}}}
        if cabin:
            travel_prefs["CabinPref"] = [{"Cabin": cabin, "PreferLevel": "Preferred"}]

        body = {
            "OTA_AirLowFareSearchRQ": {
                "Version": "4.3.0",
                "POS": {
                    "Source": [
                        {
                            "PseudoCityCode": self.pcc,
                            "RequestorID": {
                                "Type": "1",
                                "ID": "1",
                                "CompanyName": {"Code": "TN"},
                            },
                        }
                    ]
                },
                "OriginDestinationInformation": od,
                "TravelPreferences": travel_prefs,
                "TravelerInfoSummary": {
                    "SeatsRequested": [adults],
                    "AirTravelerAvail": [
                        {"PassengerTypeQuantity": [{"Code": "ADT", "Quantity": adults}]}
                    ],
                },
                "TPA_Extensions": {"IntelliSellTransaction": {"RequestType": {"Name": "50ITINS"}}},
            }
        }

        resp = requests.post(
            f"{self.base_url}/v4.3.0/shop/flights?mode=live",
            headers=self._headers(),
            json=body,
            timeout=self.timeout + 20,
        )
        if resp.status_code != 200:
            raise SabreError(f"Flight search failed ({resp.status_code}): {resp.text[:400]}")
        return _parse_offers(resp.json(), max_results)

    # ---------------------------------------------------------------- book
    def _customer_info(self, traveler: dict[str, str]) -> dict[str, Any]:
        """The shared CustomerInfo block (name / phone / email) used for every
        product's PNR. `traveler` needs first/last; email/phone optional."""
        return {
            "ContactNumbers": {
                "ContactNumber": [
                    {"Phone": traveler.get("phone", "000-000-0000"), "PhoneUseType": "H", "RPH": "1"}
                ]
            },
            "Email": [{"Address": traveler["email"], "Type": "TO"}] if traveler.get("email") else [],
            "PersonName": [
                {
                    "NameNumber": "1.1",
                    "PassengerType": "ADT",
                    "GivenName": traveler["first"],
                    "Surname": traveler["last"],
                }
            ],
        }

    def _commit_pnr(self, traveler: dict[str, str], booking_sections: dict[str, Any]) -> str:
        """Wrap one or more booking sections (AirBook/AirPrice, Hotel, Car) in a
        CreatePassengerNameRecordRQ, add the traveler + agency info, end the
        transaction, POST to Sabre, and return the record locator.

        All three products share this endpoint (`/v2.4.0/passenger/records`);
        only the `booking_sections` differ.
        """
        body = {
            "CreatePassengerNameRecordRQ": {
                "version": "2.4.0",
                "targetCity": self.pcc,
                "haltOnAirPriceError": True,
                "TravelItineraryAddInfo": {
                    "AgencyInfo": {"Ticketing": {"TicketType": "7TAW"}},
                    "CustomerInfo": self._customer_info(traveler),
                },
                **booking_sections,
                "PostProcessing": {
                    "EndTransaction": {"Source": {"ReceivedFrom": "VOICE-AGENT"}, "endTransaction": {"Ind": True}}
                },
            }
        }
        resp = requests.post(
            f"{self.base_url}/v2.4.0/passenger/records?mode=create",
            headers=self._headers(),
            json=body,
            timeout=self.timeout + 30,
        )
        if resp.status_code not in (200, 201):
            raise SabreError(f"Booking failed ({resp.status_code}): {resp.text[:500]}")
        data = resp.json().get("CreatePassengerNameRecordRS", {})
        locator = (
            data.get("ItineraryRef", {}).get("ID")
            or data.get("TravelItinerary", {}).get("ItineraryRef", {}).get("ID")
        )
        if not locator:
            raise SabreError(f"Booking returned no record locator: {str(data)[:400]}")
        return locator

    def create_pnr(
        self,
        offer: dict[str, Any],
        passenger: dict[str, str],
    ) -> dict[str, Any]:
        """Sell the offer's segments and end-transaction to get a test PNR (record locator).

        `offer` must be one of the dicts returned by search_flights (it carries the
        raw segments needed to re-sell). `passenger` needs first/last (title, email,
        phone optional).
        """
        if _mock_enabled():
            return {
                "record_locator": "MOCK" + uuid.uuid4().hex[:2].upper(),
                "status": "confirmed",
                "passenger": f"{passenger.get('first','')} {passenger.get('last','')}".strip(),
                "total_price": offer.get("total_price"),
                "currency": offer.get("currency"),
            }

        segments = []
        for seg in offer["segments"]:
            segments.append(
                {
                    "DepartureDateTime": seg["departure_time"],
                    "FlightNumber": seg["flight_number"],
                    "NumberInParty": "1",
                    "ResBookDesigCode": seg.get("booking_class", "Y"),
                    "Status": "NN",
                    "DestinationLocation": {"LocationCode": seg["destination"]},
                    "MarketingAirline": {"Code": seg["airline"], "FlightNumber": seg["flight_number"]},
                    "OriginLocation": {"LocationCode": seg["origin"]},
                }
            )

        locator = self._commit_pnr(
            passenger,
            {
                "AirBook": {
                    "OriginDestinationInformation": {"FlightSegment": segments},
                    "RedisplayReservation": {"NumAttempts": 5, "WaitInterval": 1000},
                },
                "AirPrice": [
                    {
                        "PriceRequestInformation": {
                            "OptionalQualifiers": {
                                "PricingQualifiers": {"PassengerType": [{"Code": "ADT", "Quantity": "1"}]}
                            }
                        }
                    }
                ],
            },
        )
        return {
            "record_locator": locator,
            "status": "confirmed",
            "passenger": f"{passenger['first']} {passenger['last']}",
            "total_price": offer.get("total_price"),
            "currency": offer.get("currency"),
        }

    # -------------------------------------------------------------- hotels
    def search_hotels(
        self,
        city: str,
        check_in: str,
        check_out: str,
        guests: int = 2,
        rooms: int = 1,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search hotels in a city for a date range. Returns normalised offers.

        `city` is a city name or IATA/city code; the mock ignores its specifics
        and returns a stable set of properties so the demo is deterministic.
        """
        nights = _span_days(check_in, check_out)
        if _mock_enabled():
            return _mock_hotels(city, check_in, check_out, nights, guests, rooms, max_results)

        # Live path: Sabre Hotel Availability (GetHotelAvailRQ). CERT hotel content
        # is sparse; this mirrors the flight search shape and parses best-effort.
        body = {
            "GetHotelAvailRQ": {
                "SearchCriteria": {
                    "OffSet": 1,
                    "SortBy": "TotalRate",
                    "SortOrder": "ASC",
                    "PageSize": max_results,
                    "GeoSearch": {"GeoRef": {"Radius": 30, "UOM": "MI", "RefPoint": {"Value": city.upper(), "ValueContext": "CODE", "RefPointType": "6"}}},
                    "RateInfoRef": {
                        "ConvertedRateInfoOnly": False,
                        "CurrencyCode": "USD",
                        "StayDateRange": {"StartDate": check_in, "EndDate": check_out},
                        "Rooms": {"Room": [{"Index": 1, "Adults": guests}]},
                        "InfoSource": "100,110,112,113",
                    },
                    "HotelPref": {"MaxCount": max_results},
                }
            }
        }
        resp = requests.post(
            f"{self.base_url}/v5.0.0/get/hotelavail",
            headers=self._headers(),
            json=body,
            timeout=self.timeout + 20,
        )
        if resp.status_code != 200:
            raise SabreError(f"Hotel search failed ({resp.status_code}): {resp.text[:400]}")
        return _parse_hotels(resp.json(), check_in, check_out, nights, guests, rooms, max_results)

    def create_hotel_booking(self, offer: dict[str, Any], guest: dict[str, str]) -> dict[str, Any]:
        """Reserve a hotel offer. Returns a confirmation record.

        `offer` must be one dict from search_hotels; `guest` needs first/last.
        """
        if _mock_enabled():
            return {
                "confirmation_number": "HT" + uuid.uuid4().hex[:6].upper(),
                "status": "confirmed",
                "guest": f"{guest.get('first','')} {guest.get('last','')}".strip(),
                "hotel_name": offer.get("name"),
                "check_in": offer.get("check_in"),
                "check_out": offer.get("check_out"),
                "room_type": offer.get("room_type"),
                "total_price": offer.get("total_price"),
                "currency": offer.get("currency"),
            }
        # Live path: classic direct-sell Hotel section in CreatePassengerNameRecordRQ
        # (same v2.4.0 endpoint as flights). Needs the chain code, hotel code, and a
        # rate plan / rate key captured during search_hotels.
        #
        # NOTE: implemented to the documented Create-PNR shape but UNVERIFIED against
        # live CERT (hotel content there is sparse). The leaf field names, the
        # TimeSpan date format, and whether a Guarantee/form-of-payment is required
        # may need adjusting for your CSL/GDS setup. Fails loudly rather than
        # fabricating a confirmation if identifiers are missing.
        hotel_code = offer.get("hotel_code")
        rate_plan = offer.get("rate_key") or offer.get("rate_plan_code")
        if not (hotel_code and rate_plan):
            raise SabreError("hotel offer missing hotel_code/rate key from search; cannot sell live")
        locator = self._commit_pnr(
            guest,
            {
                "Hotel": {
                    "HotelReservations": {
                        "HotelReservation": [
                            {
                                "RPH": "1",
                                "BasicPropertyInfo": {
                                    "ChainCode": offer.get("brand", ""),
                                    "HotelCode": hotel_code,
                                },
                                "RoomType": {"NumberOfUnits": str(offer.get("rooms", 1))},
                                "RatePlans": {"RatePlan": [{"RatePlanCode": rate_plan}]},
                                "GuestCounts": {"GuestCount": [{"Count": int(offer.get("guests", 1))}]},
                                "TimeSpan": {
                                    "Start": offer.get("check_in", ""),
                                    "End": offer.get("check_out", ""),
                                },
                                "Guarantee": {"GuaranteeType": "GDPST"},
                            }
                        ]
                    }
                }
            },
        )
        return {
            "confirmation_number": locator,
            "status": "confirmed",
            "guest": f"{guest['first']} {guest['last']}",
            "hotel_name": offer.get("name"),
            "check_in": offer.get("check_in"),
            "check_out": offer.get("check_out"),
            "room_type": offer.get("room_type"),
            "total_price": offer.get("total_price"),
            "currency": offer.get("currency"),
        }

    # ---------------------------------------------------------------- cars
    def search_cars(
        self,
        pickup_location: str,
        pickup_date: str,
        dropoff_date: str,
        max_results: int = 5,
    ) -> list[dict[str, Any]]:
        """Search rental cars at a location for a date range. Returns normalised offers."""
        days = _span_days(pickup_date, dropoff_date)
        if _mock_enabled():
            return _mock_cars(pickup_location, pickup_date, dropoff_date, days, max_results)

        # Live path: Sabre Cars availability (OTA_VehAvailRateRQ). Parsed best-effort.
        body = {
            "OTA_VehAvailRateRQ": {
                "Version": "4.1.0",
                "POS": {"Source": [{"PseudoCityCode": self.pcc, "RequestorID": {"Type": "5", "ID": "1", "CompanyName": {"Code": "TN"}}}]},
                "VehAvailRQCore": {
                    "VehRentalCore": {
                        "PickUpDateTime": f"{pickup_date}T10:00:00",
                        "ReturnDateTime": f"{dropoff_date}T10:00:00",
                        "PickUpLocation": {"LocationCode": pickup_location.upper()},
                        "ReturnLocation": {"LocationCode": pickup_location.upper()},
                    }
                },
            }
        }
        resp = requests.post(
            f"{self.base_url}/v4.1.0/shop/cars",
            headers=self._headers(),
            json=body,
            timeout=self.timeout + 20,
        )
        if resp.status_code != 200:
            raise SabreError(f"Car search failed ({resp.status_code}): {resp.text[:400]}")
        return _parse_cars(resp.json(), pickup_location, pickup_date, dropoff_date, days, max_results)

    def create_car_booking(self, offer: dict[str, Any], driver: dict[str, str]) -> dict[str, Any]:
        """Reserve a rental-car offer. Returns a confirmation record.

        `offer` must be one dict from search_cars; `driver` needs first/last.
        """
        if _mock_enabled():
            return {
                "confirmation_number": "CR" + uuid.uuid4().hex[:6].upper(),
                "status": "confirmed",
                "driver": f"{driver.get('first','')} {driver.get('last','')}".strip(),
                "vendor_name": offer.get("vendor_name"),
                "car_type": offer.get("car_type"),
                "pickup_date": offer.get("pickup_date"),
                "dropoff_date": offer.get("dropoff_date"),
                "total_price": offer.get("total_price"),
                "currency": offer.get("currency"),
            }
        # Live path: classic Car section in CreatePassengerNameRecordRQ (same v2.4.0
        # endpoint). Needs the vendor code and rate/booking code captured during
        # search_cars.
        #
        # NOTE: implemented to the documented Create-PNR shape but UNVERIFIED against
        # live CERT; leaf field names (VendorPref/VehPref/RateCode) and the pickup
        # time may need adjusting for your configuration.
        rate_code = offer.get("rate_code") or offer.get("booking_code") or ""
        locator = self._commit_pnr(
            driver,
            {
                "Car": {
                    "CarReservations": {
                        "CarReservation": [
                            {
                                "RPH": "1",
                                "PickUp": {
                                    "DateTime": f"{offer.get('pickup_date','')}T10:00",
                                    "LocationCode": offer.get("pickup_location", ""),
                                },
                                "Return": {
                                    "DateTime": f"{offer.get('dropoff_date','')}T10:00",
                                    "LocationCode": offer.get("dropoff_location", offer.get("pickup_location", "")),
                                },
                                "VendorPref": {"Code": offer.get("vendor", "")},
                                "VehPref": {"CarType": offer.get("car_type_code", "")},
                                "RateCode": rate_code,
                            }
                        ]
                    }
                }
            },
        )
        return {
            "confirmation_number": locator,
            "status": "confirmed",
            "driver": f"{driver['first']} {driver['last']}",
            "vendor_name": offer.get("vendor_name"),
            "car_type": offer.get("car_type"),
            "pickup_date": offer.get("pickup_date"),
            "dropoff_date": offer.get("dropoff_date"),
            "total_price": offer.get("total_price"),
            "currency": offer.get("currency"),
        }


# ------------------------------------------------------------------ parsing
def _parse_offers(payload: dict[str, Any], max_results: int) -> list[dict[str, Any]]:
    rs = payload.get("OTA_AirLowFareSearchRS", {})
    priced = rs.get("PricedItineraries", {}).get("PricedItinerary", []) or []
    offers: list[dict[str, Any]] = []
    for idx, itin in enumerate(priced[:max_results]):
        try:
            air = itin["AirItinerary"]
            od_options = air["OriginDestinationOptions"]["OriginDestinationOption"]
            segments: list[dict[str, Any]] = []
            for od in od_options:
                for seg in od["FlightSegment"]:
                    segments.append(
                        {
                            "airline": seg["MarketingAirline"]["Code"],
                            "flight_number": str(seg.get("FlightNumber", "")),
                            "origin": seg["DepartureAirport"]["LocationCode"],
                            "destination": seg["ArrivalAirport"]["LocationCode"],
                            "departure_time": seg["DepartureDateTime"],
                            "arrival_time": seg["ArrivalDateTime"],
                            "booking_class": seg.get("ResBookDesigCode", "Y"),
                        }
                    )
            fare = itin["AirItineraryPricingInfo"]["ItinTotalFare"]["TotalFare"]
        except (KeyError, IndexError, TypeError):
            continue
        total = fare.get("Amount")
        currency = fare.get("CurrencyCode")
        offers.append(
            {
                "offer_id": str(idx),
                "airline": segments[0]["airline"] if segments else "",
                "segments": segments,
                "stops": max(len(segments) - 1, 0),
                "total_price": float(total) if total is not None else None,
                "currency": currency,
                "summary": _summarize(segments, total, currency),
            }
        )
    return offers


def _summarize(segments: list[dict[str, Any]], total: Any, currency: str | None, stops: int | None = None) -> str:
    if not segments:
        return "No itinerary details."
    first, last = segments[0], segments[-1]
    if stops is None:
        stops = max(len(segments) - 1, 0)
    stop_txt = "nonstop" if stops == 0 else f"{stops} stop" + ("s" if stops > 1 else "")
    price = f"{total} {currency}" if total is not None else "price unavailable"
    return (
        f"{first['airline']}{first['flight_number']} {first['origin']}->{last['destination']}, "
        f"departs {first['departure_time']}, {stop_txt}, {price}"
    )


# --------------------------------------------------------------------- mock
def _mk_seg(al, fn, orig, dest, date, dep, arr):
    return {
        "airline": al,
        "flight_number": str(fn),
        "origin": orig,
        "destination": dest,
        "departure_time": f"{date}T{dep}",
        "arrival_time": f"{date}T{arr}",
        "booking_class": "Y",
    }


def _mock_offers(origin, destination, departure_date, return_date, max_results):
    # (airline, flight_no, depart, arrive, stops, one-way price)
    base = [
        ("DL", 412, "08:15:00", "11:40:00", 0, 289.40),
        ("AA", 178, "12:05:00", "18:22:00", 1, 214.10),
        ("UA", 533, "17:30:00", "20:55:00", 0, 331.75),
        ("B6", 621, "06:00:00", "12:48:00", 1, 198.60),
        ("AS", 9, "21:10:00", "23:59:00", 0, 356.20),
    ]
    origin, destination = origin.upper(), destination.upper()
    hub = "DEN"  # fictional connection point for 1-stop itineraries
    offers = []
    for idx, (al, fn, dep, arr, stops, price) in enumerate(base[:max_results]):
        segs = []
        # outbound
        if stops == 0:
            segs.append(_mk_seg(al, fn, origin, destination, departure_date, dep, arr))
        else:
            segs.append(_mk_seg(al, fn, origin, hub, departure_date, dep, "15:10:00"))
            segs.append(_mk_seg(al, fn + 1, hub, destination, departure_date, "16:05:00", arr))
        # return
        if return_date:
            price = round(price * 1.9, 2)  # round-trip fare
            if stops == 0:
                segs.append(_mk_seg(al, fn + 50, destination, origin, return_date, "09:30:00", "17:05:00"))
            else:
                segs.append(_mk_seg(al, fn + 50, destination, hub, return_date, "09:30:00", "12:40:00"))
                segs.append(_mk_seg(al, fn + 51, hub, origin, return_date, "13:35:00", "17:05:00"))
        offers.append(
            {
                "offer_id": str(idx),
                "airline": al,
                "segments": segs,
                "stops": stops,
                "total_price": price,
                "currency": "USD",
                "summary": _summarize(segs, price, "USD", stops=stops),
            }
        )
    return offers


# ----------------------------------------------------------- hotel parsing
def _hotel_summary(o: dict[str, Any]) -> str:
    chain = f" ({o['brand_name']})" if o.get("brand_name") else ""
    stars = f"{o['rating']}-star, " if o.get("rating") else ""
    rate = f"${o['nightly_rate']}/night" if o.get("nightly_rate") is not None else "rate on request"
    total = f"{o['total_price']} {o['currency']}" if o.get("total_price") is not None else "total unavailable"
    nights = o.get("nights", 1)
    return (
        f"{o['name']}{chain}, {stars}{o.get('room_type','room')}, "
        f"{rate}, {nights} night{'s' if nights != 1 else ''} = {total}"
    )


def _parse_hotels(payload, check_in, check_out, nights, guests, rooms, max_results):
    """Best-effort parse of GetHotelAvailRS into voice-friendly offers."""
    rs = payload.get("GetHotelAvailRS", {})
    props = (rs.get("HotelAvailInfos", {}) or {}).get("HotelAvailInfo", []) or []
    offers: list[dict[str, Any]] = []
    for idx, prop in enumerate(props[:max_results]):
        try:
            info = prop.get("HotelInfo", {})
            rate = (((prop.get("HotelRateInfo", {}) or {}).get("RateInfos", {}) or {})
                    .get("RateInfo", [{}]) or [{}])[0]
            nightly = rate.get("AmountAfterTax") or rate.get("AmountBeforeTax")
            nightly = float(nightly) if nightly is not None else None
            chain = info.get("ChainCode", "")
            offers.append(
                {
                    "offer_id": str(idx),
                    "type": "hotel",
                    "name": info.get("HotelName", "Hotel"),
                    "brand": chain,
                    "brand_name": hotel_chain_name(chain),
                    "hotel_code": info.get("HotelCode") or info.get("SabreHotelCode"),
                    "rating": _int_or_none(info.get("SabreRating") or info.get("HotelRating")),
                    "address": _hotel_address(info),
                    "city": (info.get("LocationInfo", {}) or {}).get("Address", {}).get("CityName", ""),
                    "check_in": check_in,
                    "check_out": check_out,
                    "nights": nights,
                    "rooms": rooms,
                    "guests": guests,
                    "room_type": rate.get("RoomTypeName") or "Room",
                    "nightly_rate": nightly,
                    "total_price": round(nightly * nights, 2) if nightly is not None else None,
                    "currency": rate.get("CurrencyCode", "USD"),
                    "rate_key": rate.get("RateKey"),
                }
            )
        except (KeyError, IndexError, TypeError, ValueError):
            continue
    for o in offers:
        o["summary"] = _hotel_summary(o)
    return offers


def _hotel_address(info: dict) -> str:
    addr = (info.get("LocationInfo", {}) or {}).get("Address", {}) or {}
    parts = [addr.get("AddressLine1") or "", addr.get("CityName") or "", addr.get("StateProv", {}).get("StateCode", "") if isinstance(addr.get("StateProv"), dict) else ""]
    return ", ".join(p for p in parts if p)


def _int_or_none(v):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------- car parsing
def _car_summary(o: dict[str, Any]) -> str:
    vendor = o.get("vendor_name") or "Car"
    trans = (o.get("transmission") or "").lower()
    trans_txt = f", {trans}" if trans else ""
    rate = f"${o['daily_rate']}/day" if o.get("daily_rate") is not None else "rate on request"
    total = f"{o['total_price']} {o['currency']}" if o.get("total_price") is not None else "total unavailable"
    days = o.get("days", 1)
    return f"{vendor} {o.get('car_type','vehicle')}{trans_txt}, {rate}, {days} day{'s' if days != 1 else ''} = {total}"


def _parse_cars(payload, pickup_location, pickup_date, dropoff_date, days, max_results):
    """Best-effort parse of OTA_VehAvailRateRS into voice-friendly offers."""
    rs = payload.get("OTA_VehAvailRateRS", {})
    core = rs.get("VehAvailRSCore", {}) or {}
    vehicles = (core.get("VehVendorAvails", {}) or {}).get("VehVendorAvail", []) or []
    offers: list[dict[str, Any]] = []
    idx = 0
    for vendor_block in vehicles:
        if idx >= max_results:
            break
        vendor = (vendor_block.get("Vendor", {}) or {}).get("Code", "")
        avails = (vendor_block.get("VehAvails", {}) or {}).get("VehAvail", []) or []
        for av in avails:
            if idx >= max_results:
                break
            try:
                v = (av.get("VehAvailCore", {}) or {})
                veh = v.get("Vehicle", {}) or {}
                total = (((v.get("TotalCharge", {}) or {}).get("EstimatedTotalAmount"))
                         or (v.get("TotalCharge", {}) or {}).get("RateTotalAmount"))
                total = float(total) if total is not None else None
                offers.append(
                    {
                        "offer_id": str(idx),
                        "type": "car",
                        "vendor": vendor,
                        "vendor_name": car_vendor_name(vendor),
                        "rate_code": (v.get("Reference", {}) or {}).get("ID") or (v.get("RentalRate", {}) or {}).get("RateCode"),
                        "car_type_code": (veh.get("VehType", {}) or {}).get("VehicleCategory"),
                        "car_type": (veh.get("VehType", {}) or {}).get("VehicleCategory") or veh.get("VehClass", {}).get("Size") or "Vehicle",
                        "transmission": veh.get("TransmissionType", "Automatic"),
                        "air_conditioning": bool(veh.get("AirCondition", True)),
                        "pickup_location": pickup_location.upper(),
                        "dropoff_location": pickup_location.upper(),
                        "pickup_date": pickup_date,
                        "dropoff_date": dropoff_date,
                        "days": days,
                        "daily_rate": round(total / days, 2) if total is not None else None,
                        "total_price": total,
                        "currency": (v.get("TotalCharge", {}) or {}).get("CurrencyCode", "USD"),
                    }
                )
                idx += 1
            except (KeyError, IndexError, TypeError, ValueError):
                continue
    for o in offers:
        o["summary"] = _car_summary(o)
    return offers


# --------------------------------------------------------------- hotel mock
def _mock_hotels(city, check_in, check_out, nights, guests, rooms, max_results):
    # (name, chain code, stars, nightly rate, room type)
    base = [
        ("Grand Plaza Hotel", "HL", 4, 189.0, "King Room"),
        ("Harborview Suites", "MC", 5, 279.0, "Executive Suite"),
        ("The Marketside", "HY", 4, 215.0, "King Room"),
        ("Cityline Inn", "HI", 3, 119.0, "Double Queen"),
        ("Riverside Lodge", "BW", 3, 99.0, "Standard Room"),
    ]
    city_disp = (city or "").strip().upper()
    offers = []
    for idx, (name, chain, stars, rate, room) in enumerate(base[:max_results]):
        total = round(rate * nights * rooms, 2)
        o = {
            "offer_id": str(idx),
            "type": "hotel",
            "name": name,
            "brand": chain,
            "brand_name": hotel_chain_name(chain),
            "rating": stars,
            "address": f"{100 + idx * 7} Main St, {city_disp}",
            "city": city_disp,
            "check_in": check_in,
            "check_out": check_out,
            "nights": nights,
            "rooms": rooms,
            "guests": guests,
            "room_type": room,
            "nightly_rate": rate,
            "total_price": total,
            "currency": "USD",
            "rate_key": f"MOCK-{idx}",
        }
        o["summary"] = _hotel_summary(o)
        offers.append(o)
    return offers


# ----------------------------------------------------------------- car mock
def _mock_cars(pickup_location, pickup_date, dropoff_date, days, max_results):
    # (vendor code, car type, transmission, daily rate)
    base = [
        ("ZE", "Intermediate SUV", "Automatic", 54.0),
        ("ZI", "Compact", "Automatic", 39.0),
        ("ET", "Full-size Sedan", "Automatic", 62.0),
        ("ZR", "Premium SUV", "Automatic", 89.0),
        ("ZD", "Economy", "Manual", 33.0),
    ]
    loc = (pickup_location or "").strip().upper()
    offers = []
    for idx, (vendor, car_type, trans, rate) in enumerate(base[:max_results]):
        total = round(rate * days, 2)
        o = {
            "offer_id": str(idx),
            "type": "car",
            "vendor": vendor,
            "vendor_name": car_vendor_name(vendor),
            "car_type": car_type,
            "transmission": trans,
            "air_conditioning": True,
            "pickup_location": loc,
            "dropoff_location": loc,
            "pickup_date": pickup_date,
            "dropoff_date": dropoff_date,
            "days": days,
            "daily_rate": rate,
            "total_price": total,
            "currency": "USD",
        }
        o["summary"] = _car_summary(o)
        offers.append(o)
    return offers
