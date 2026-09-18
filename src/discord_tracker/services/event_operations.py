import os
from datetime import datetime
from discord_tracker.integrations.wise_old_man import IntegrationError


def matches(event, result, require_title=True):
    try:
        return (isinstance(result.get('id'), int) and result['groupId'] == event['group_id']
                and result['metric'] == event['winner']
                and (not require_title or result['title'] == (event['request_title'] or
                     f"{event['kind'].title()} of the Week [event {event['id']}]"))
                and abs(datetime.fromisoformat(result['startsAt'].replace('Z', '+00:00')).timestamp() - event['starts']) < 1
                and abs(datetime.fromisoformat(result['endsAt'].replace('Z', '+00:00')).timestamp() - event['ends']) < 1)
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


class EventOperations:
    def __init__(self, bot):
        self.bot = bot

    async def reconcile(self, event_id):
        event = self.bot.competitions.get(event_id)
        candidates = await self.bot.wom.group_competitions(event['group_id'])
        candidates = [c for c in candidates if matches(event, c)]
        if len(candidates) > 1:
            raise ValueError('Multiple matching competitions; manager must select the correct one with /event-reconcile')
        if not candidates:
            return None
        candidate = await self.bot.wom.request('GET', f"competitions/{candidates[0]['id']}")
        if not matches(event, candidate):
            raise ValueError('Competition changed during reconciliation; manager review required')
        with self.bot.db.connection:
            self.bot.db.connection.execute("UPDATE events SET competition_id=?,state='ready',error=NULL WHERE id=?",
                                           (candidate['id'], event_id))
        self.bot.db.audit('scheduler', 'competition-reconciled', event_id, str(candidate['id']))
        return candidate['id']

    async def cancel(self, event_id, actor, reason, delete_remote=False, confirm_id=None):
        event = self.bot.competitions.get(event_id)
        if not reason.strip():
            raise ValueError('A reason is required')
        if event['state'] == 'completed':
            raise ValueError('Completed events cannot be cancelled through this command')
        if event['state'] == 'cancelled' and not delete_remote:
            raise ValueError('Already cancelled locally. Use confirmed remote deletion if external cleanup is needed')
        if delete_remote:
            if event['competition_id'] is None:
                raise ValueError('No known competition ID. Reconcile uncertain creation before requesting deletion')
            if confirm_id != event['competition_id']:
                raise ValueError(f"Deletion is irreversible. Confirm by supplying confirm_competition_id:{event['competition_id']}")
            if not os.getenv('WOM_GROUP_VERIFICATION_CODE'):
                raise ValueError('Operator must configure WOM_GROUP_VERIFICATION_CODE before deletion')
        # Commit the local cancellation before any external call. Failed deletion
        # must never resume scheduling or resurrect the competition.
        with self.bot.db.connection:
            self.bot.db.connection.execute("UPDATE events SET state='cancelled',deletion_state=?,error=NULL WHERE id=?",
                                           ('pending' if delete_remote else event['deletion_state'], event_id))
            if event['next_event_id']:
                self.bot.db.connection.execute("UPDATE events SET state='cancelled' WHERE id=? AND state='scheduled'", (event['next_event_id'],))
        self.bot.db.audit(actor, 'event-cancel', event_id, reason[:500])
        remote_result = 'External competition retained.' if event['competition_id'] else 'No external competition ID is recorded; review any uncertain creation.'
        if delete_remote:
            try:
                await self.delete_remote(event)
            except Exception as exc:
                with self.bot.db.connection:
                    self.bot.db.connection.execute("UPDATE events SET deletion_state='unknown',error=? WHERE id=?",
                                                   ('Deletion not confirmed. Retry confirmed cancellation to check remote state.', event_id))
                self.bot.db.audit(actor, 'competition-delete', event_id, f'unconfirmed ({type(exc).__name__})')
                self.bot.alerts.enqueue(f'Event #{event_id}: local cancellation completed, but Wise Old Man deletion is unconfirmed. '
                                        'Check /events and repeat /event-cancel with the competition ID after review.')
                remote_result = 'PARTIAL: Wise Old Man deletion is unconfirmed. Retry confirmed cancellation to reconcile it.'
            else:
                with self.bot.db.connection:
                    self.bot.db.connection.execute("UPDATE events SET deletion_state='deleted',error=NULL WHERE id=?", (event_id,))
                self.bot.db.audit(actor, 'competition-delete', event_id, 'deleted or already absent')
                remote_result = 'Wise Old Man competition deleted or already absent.'
        poll_result = ''
        if event['message_id']:
            try:
                message = await self.bot.competition_worker.message(event)
                await message.edit(content=f'Poll #{event_id} cancelled. Voting is closed.')
            except Exception as exc:
                self.bot.db.audit(actor, 'cancel-poll-message', event_id, type(exc).__name__)
                self.bot.alerts.enqueue(f'Event #{event_id}: cancelled locally, but the poll message could not be edited. Voting is disabled; inspect its channel.')
                poll_result = ' PARTIAL: poll message could not be edited; voting is disabled.'
        return f'Local automation cancelled. {remote_result}{poll_result}'

    async def delete_remote(self, event):
        path = f"competitions/{event['competition_id']}"
        try:
            existing = await self.bot.wom.request('GET', path)
        except IntegrationError as exc:
            if exc.status == 404:
                return  # A previous timed-out deletion may already have succeeded.
            raise
        if not matches(event, existing, require_title=False):
            raise ValueError('Remote competition identity does not match the stored event; deletion refused')
        try:
            await self.bot.wom.request('DELETE', path, {'verificationCode': os.environ['WOM_GROUP_VERIFICATION_CODE']})
        except IntegrationError as exc:
            if exc.status == 404:
                return
            raise
