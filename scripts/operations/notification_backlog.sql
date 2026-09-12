-- Read-only, aggregate-only operator inspection. No user/occurrence IDs or content.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
SELECT now() AS observed_at,
       count(*) FILTER (WHERE n.scheduled_at <= now() AND o.status = 'PENDING'
                        AND o.confirmation_deadline_at > now()) AS due_pending,
       count(*) FILTER (WHERE o.status <> 'PENDING'
                        OR o.confirmation_deadline_at <= now()) AS ineligible_pending,
       min(n.scheduled_at) FILTER (WHERE n.scheduled_at <= now() AND o.status = 'PENDING'
                                  AND o.confirmation_deadline_at > now()) AS oldest_due_at
FROM notification_record n
JOIN medication_occurrence o ON o.id = n.occurrence_id
WHERE n.status = 'PENDING';
SELECT count(*) AS ungenerated_eligible,
       count(*) FILTER (WHERE o.scheduled_at <= now()) AS ungenerated_due,
       min(o.scheduled_at) FILTER (WHERE o.scheduled_at <= now()) AS oldest_ungenerated_due_at
FROM medication_occurrence o
WHERE o.status = 'PENDING' AND o.confirmation_deadline_at > now()
  AND NOT EXISTS (SELECT 1 FROM notification_record n
                  WHERE n.occurrence_id = o.id AND n.kind = 'SCHEDULED');
COMMIT;
