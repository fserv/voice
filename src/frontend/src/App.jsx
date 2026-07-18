import { useEffect, useRef, useState } from 'react';
import { useVocalBridge, useTranscript, useAgentActions } from '@vocalbridgeai/react';

// Format an ISO timestamp like "2026-08-08T12:05:00" -> "Aug 8, 12:05 PM".
function fmt(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d)) return iso;
  return d.toLocaleString(undefined, {
    month: 'short',
    day: 'numeric',
    hour: 'numeric',
    minute: '2-digit',
  });
}

// Format a date-only string like "2026-08-08" -> "Aug 8".
function fmtDate(iso) {
  if (!iso) return '';
  const d = new Date(`${iso}T00:00:00`);
  if (isNaN(d)) return iso;
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function money(v) {
  if (v == null) return '—';
  return `$${Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 })}`;
}

function hideBroken(e) {
  e.target.style.display = 'none';
}

function stopsLabel(n) {
  return n === 0 ? 'Nonstop' : `${n} stop${n > 1 ? 's' : ''}`;
}

function stars(n) {
  const count = Math.max(0, Math.min(5, Number(n) || 0));
  return '★'.repeat(count) + '☆'.repeat(5 - count);
}

function ItineraryTable({ segments }) {
  return (
    <table className="itin">
      <thead>
        <tr>
          <th>Flight</th>
          <th>From</th>
          <th>Departs</th>
          <th>To</th>
          <th>Arrives</th>
        </tr>
      </thead>
      <tbody>
        {segments.map((s, i) => (
          <tr key={i}>
            <td className="flt">
              <img className="mini-logo" src={s.logo_url} alt={s.airline} onError={hideBroken} />
              {s.airline}
              {s.flight_number}
            </td>
            <td>{s.origin}</td>
            <td>{fmt(s.departure_time)}</td>
            <td>{s.destination}</td>
            <td>{fmt(s.arrival_time)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

function OfferCard({ offer }) {
  const segs = offer.segments || [];
  const route = segs.length ? `${segs[0].origin} → ${segs[segs.length - 1].destination}` : '';
  return (
    <div className="card">
      <div className="card-head">
        <img className="logo" src={offer.logo_url} alt={offer.airline_name} onError={hideBroken} />
        <div className="head-text">
          <span className="badge">Option {Number(offer.offer_id) + 1}</span>
          <span className="airline-name">{offer.airline_name}</span>
          <span className="route">{route}</span>
        </div>
        <div className="price-box">
          <span className="price">{money(offer.total_price)}</span>
          <span className="stops">{stopsLabel(offer.stops)}</span>
        </div>
      </div>
      <ItineraryTable segments={segs} />
      <img className="plane-photo" src={offer.photo_url} alt="aircraft" loading="lazy" onError={hideBroken} />
    </div>
  );
}

function HotelCard({ offer }) {
  return (
    <div className="card">
      <div className="card-head">
        <div className="head-text">
          <span className="badge">Option {Number(offer.offer_id) + 1}</span>
          <span className="airline-name">{offer.name}</span>
          <span className="route">
            {offer.brand_name ? `${offer.brand_name} · ` : ''}
            <span className="stars">{stars(offer.rating)}</span> · {offer.room_type}
          </span>
          <span className="route">
            {fmtDate(offer.check_in)}–{fmtDate(offer.check_out)} · {offer.nights} night
            {offer.nights === 1 ? '' : 's'}
          </span>
        </div>
        <div className="price-box">
          <span className="price">{money(offer.total_price)}</span>
          <span className="stops">{money(offer.nightly_rate)}/night</span>
        </div>
      </div>
      <img className="plane-photo" src={offer.photo_url} alt={offer.name} loading="lazy" onError={hideBroken} />
    </div>
  );
}

function CarCard({ offer }) {
  return (
    <div className="card">
      <div className="card-head">
        <div className="head-text">
          <span className="badge">Option {Number(offer.offer_id) + 1}</span>
          <span className="airline-name">{offer.vendor_name}</span>
          <span className="route">
            {offer.car_type} · {offer.transmission}
          </span>
          <span className="route">
            {fmtDate(offer.pickup_date)}–{fmtDate(offer.dropoff_date)} · {offer.days} day
            {offer.days === 1 ? '' : 's'}
          </span>
        </div>
        <div className="price-box">
          <span className="price">{money(offer.total_price)}</span>
          <span className="stops">{money(offer.daily_rate)}/day</span>
        </div>
      </div>
      <img className="plane-photo" src={offer.photo_url} alt={offer.car_type} loading="lazy" onError={hideBroken} />
    </div>
  );
}

// A booked confirmation card, reused for hotels and cars.
function ConfirmationCard({ title, confirmation, name, detail, photoUrl, total }) {
  return (
    <div className="card confirmed">
      <div className="confirmed-head">
        <div>
          <h3>✅ {title}</h3>
          <p>
            Confirmation: <strong className="locator">{confirmation}</strong>
          </p>
          <p className="muted">
            {name}
            {detail ? ` · ${detail}` : ''}
          </p>
        </div>
        <span className="price">{money(total)}</span>
      </div>
      {photoUrl && (
        <img className="plane-photo" src={photoUrl} alt={title} loading="lazy" onError={hideBroken} />
      )}
    </div>
  );
}

export default function App() {
  const { state, connect, disconnect, toggleMicrophone, isMicrophoneEnabled, sendAction } =
    useVocalBridge();
  const { transcript } = useTranscript();
  useAgentActions(); // provider requires the hook to be mounted

  const [offers, setOffers] = useState([]);
  const [booking, setBooking] = useState(null);
  const [hotels, setHotels] = useState([]);
  const [hotelBooking, setHotelBooking] = useState(null);
  const [cars, setCars] = useState([]);
  const [carBooking, setCarBooking] = useState(null);
  const sessionRef = useRef(null);
  const transcriptEndRef = useRef(null);

  const connected = state === 'connected';

  // On connect: mint a session id and hand it to the agent.
  useEffect(() => {
    if (!connected || sessionRef.current) return;
    (async () => {
      try {
        const res = await fetch('/api/session');
        const { session_id } = await res.json();
        sessionRef.current = session_id;
        await sendAction('set_session', { session_id });
      } catch (e) {
        console.warn('session setup failed', e);
      }
    })();
  }, [connected, sendAction]);

  // Poll the backend for the current itinerary/booking state.
  useEffect(() => {
    if (!connected) return;
    let alive = true;
    const tick = async () => {
      const sid = sessionRef.current;
      if (!sid) return;
      try {
        const res = await fetch(`/api/state?session_id=${encodeURIComponent(sid)}`);
        const data = await res.json();
        if (!alive) return;
        setOffers(data.offers || []);
        setBooking(data.booking || null);
        setHotels(data.hotels || []);
        setHotelBooking(data.hotel_booking || null);
        setCars(data.cars || []);
        setCarBooking(data.car_booking || null);
      } catch {
        /* ignore transient poll errors */
      }
    };
    const id = setInterval(tick, 1500);
    tick();
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [connected]);

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [transcript]);

  const bookedOffer = booking?.offer;
  const bookedHotel = hotelBooking?.offer;
  const bookedCar = carBooking?.offer;
  const nothingYet =
    !booking && !hotelBooking && !carBooking &&
    offers.length === 0 && hotels.length === 0 && cars.length === 0;

  return (
    <div className="app">
        <img className="omegaphototop" src="/src/omegatop.png" alt="omega" loading="lazy"  />

      <div className="controls">
        {!connected ? (
          <button className="primary" onClick={connect} disabled={state === 'connecting'}>
            {state === 'connecting' ? 'Connecting…' : 'Start talking'}
          </button>
        ) : (
          <>
            <button onClick={toggleMicrophone}>
              {isMicrophoneEnabled ? '🎙️ Mute' : '🔇 Unmute'}
            </button>
            <button className="danger" onClick={disconnect}>
              End call
            </button>
            <span className="status live">● live</span>
          </>
        )}
      </div>

      <div className="panels">
        <section className="transcript">
          <h2>Conversation</h2>
          {transcript.length === 0 && <p className="muted">Say hello to get started…</p>}
          {transcript.map((e, i) => (
            <p key={i} className={e.role === 'user' ? 'you' : 'agent'}>
              <strong>{e.role === 'user' ? 'You' : 'Skylark'}:</strong> said: {e.text}
            </p>
          ))}
          <div ref={transcriptEndRef} />
        </section>

        <section className="results">
          <h2>Your trip</h2>

          {nothingYet && (
            <p className="muted">Flight, hotel, and car options appear here as you search.</p>
          )}

          {/* Flights */}
          {(booking || offers.length > 0) && <h3 className="group-title">✈️ Flights</h3>}
          {booking && (
            <div className="card confirmed">
              <div className="confirmed-head">
                {bookedOffer && (
                  <img
                    className="logo"
                    src={bookedOffer.logo_url}
                    alt={bookedOffer.airline_name}
                    onError={hideBroken}
                  />
                )}
                <div>
                  <h3>✅ Booked</h3>
                  <p>
                    Confirmation: <strong className="locator">{booking.record_locator}</strong>
                  </p>
                  <p className="muted">{booking.passenger}</p>
                </div>
                <span className="price">{money(booking.total_price)}</span>
              </div>
              {bookedOffer && <ItineraryTable segments={bookedOffer.segments} />}
              {bookedOffer && (
                <img
                  className="plane-photo"
                  src={bookedOffer.photo_url}
                  alt="aircraft"
                  loading="lazy"
                  onError={hideBroken}
                />
              )}
            </div>
          )}
          {!booking && offers.map((o) => <OfferCard key={o.offer_id} offer={o} />)}

          {/* Hotels */}
          {(hotelBooking || hotels.length > 0) && <h3 className="group-title">🏨 Hotels</h3>}
          {hotelBooking && (
            <ConfirmationCard
              title="Hotel booked"
              confirmation={hotelBooking.confirmation_number}
              name={hotelBooking.guest}
              detail={`${hotelBooking.hotel_name}${
                hotelBooking.room_type ? `, ${hotelBooking.room_type}` : ''
              }`}
              photoUrl={bookedHotel?.photo_url}
              total={hotelBooking.total_price}
            />
          )}
          {!hotelBooking && hotels.map((o) => <HotelCard key={o.offer_id} offer={o} />)}

          {/* Cars */}
          {(carBooking || cars.length > 0) && <h3 className="group-title">🚗 Rental cars</h3>}
          {carBooking && (
            <ConfirmationCard
              title="Car booked"
              confirmation={carBooking.confirmation_number}
              name={carBooking.driver}
              detail={`${carBooking.vendor_name}, ${carBooking.car_type}`}
              photoUrl={bookedCar?.photo_url}
              total={carBooking.total_price}
            />
          )}
          {!carBooking && cars.map((o) => <CarCard key={o.offer_id} offer={o} />)}
        </section>


      </div>

      <center>
        <img className="omegaphotobot" src="/src/omegabottom.png" alt="omegabot" loading="lazy"  />
      </center>

    </div>
  );
}
