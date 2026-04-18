// Renders a CardsEvent — products, payment methods, appointment
// slots, and existing appointments.
//   - Product cards: details + explicit "Order now" CTA.
//   - Appointment cards: details + "Reschedule" / "Cancel" CTAs.
//   - Payment method / appointment slot cards: auto-submit a
//     confirmation query on click (the customer already saw the
//     prompt "Which payment method?" / "Pick a time" above the grid,
//     so tapping a card is the answer — making them press Enter
//     again is pure friction).
import type { CardItem, CardsEvent } from "@/api/types"
import { Button } from "@/components/ui/button"

interface CardGridProps {
  event: CardsEvent
  // Called when a payment method / appointment slot card is clicked.
  // Parent auto-submits a confirmation turn so the specialist can
  // advance the flow (e.g. to propose_place_order) without requiring
  // the customer to press Send.
  onSelect: (card: CardItem) => void
  // Fired from a product card's "Order now" button. Parent submits a
  // new turn directly so the order specialist can start the flow.
  onOrderNow?: (card: CardItem) => void
  // Fired from an appointment card's CTAs. Parent auto-submits a
  // reschedule / cancel intent so the appointment specialist can
  // drive the flow (show new slots, propose_cancel, etc.).
  onAppointmentReschedule?: (card: CardItem) => void
  onAppointmentCancel?: (card: CardItem) => void
}

export function CardGrid({
  event,
  onSelect,
  onOrderNow,
  onAppointmentReschedule,
  onAppointmentCancel,
}: CardGridProps) {
  const isProduct = event.kind === "product"
  const isAppointment = event.kind === "appointment"
  const cols = isProduct ? "grid-cols-2" : "grid-cols-1"
  return (
    <div className="mt-3">
      {event.prompt && (
        <p className="text-sm font-medium mb-2">{event.prompt}</p>
      )}
      <div className={`grid gap-2 ${cols}`}>
        {event.cards.map((card) =>
          isProduct ? (
            <ProductCard
              key={`${card.kind}-${card.id}`}
              card={card}
              onOrderNow={() => onOrderNow?.(card)}
            />
          ) : isAppointment ? (
            <AppointmentCard
              key={`${card.kind}-${card.id}`}
              card={card}
              onReschedule={() => onAppointmentReschedule?.(card)}
              onCancel={() => onAppointmentCancel?.(card)}
            />
          ) : (
            <button
              key={`${card.kind}-${card.id}`}
              onClick={() => onSelect(card)}
              className="text-left border rounded-lg p-3 hover:bg-muted/60 transition-colors flex gap-3 items-start"
            >
              <CardBody card={card} />
            </button>
          ),
        )}
      </div>
    </div>
  )
}

function AppointmentCard({
  card,
  onReschedule,
  onCancel,
}: {
  card: CardItem
  onReschedule: () => void
  onCancel: () => void
}) {
  const status = String(card.metadata?.status ?? "").toLowerCase()
  const actionable = status === "booked" || status === ""
  return (
    <div className="border rounded-lg p-3 flex flex-col gap-3">
      <CardBody card={card} />
      {actionable && (
        <div className="flex gap-2">
          <Button size="sm" variant="outline" onClick={onReschedule}>
            Reschedule
          </Button>
          <Button size="sm" variant="destructive" onClick={onCancel}>
            Cancel
          </Button>
        </div>
      )}
    </div>
  )
}

function ProductCard({
  card,
  onOrderNow,
}: {
  card: CardItem
  onOrderNow: () => void
}) {
  return (
    <div className="border rounded-lg p-3 flex flex-col gap-3">
      <CardBody card={card} />
      <Button size="sm" onClick={onOrderNow} className="self-start">
        Order now
      </Button>
    </div>
  )
}

function CardBody({ card }: { card: CardItem }) {
  return (
    <div className="flex gap-3 items-start w-full">
      {card.image_url && (
        <img
          src={card.image_url}
          alt=""
          className="w-14 h-14 object-cover rounded shrink-0"
          loading="lazy"
        />
      )}
      <div className="flex-1 min-w-0">
        <div className="font-medium text-sm">{card.title}</div>
        {card.subtitle && (
          <div className="text-xs text-muted-foreground mt-0.5">
            {card.subtitle}
          </div>
        )}
        {card.badges && card.badges.length > 0 && (
          <div className="flex flex-wrap gap-1 mt-1">
            {card.badges.map((b, i) => (
              <span
                key={i}
                className="text-[10px] bg-primary/10 text-primary px-1.5 py-0.5 rounded"
              >
                {b}
              </span>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
