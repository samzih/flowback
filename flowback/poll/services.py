from django.db.models import Sum, Q, Count, F
from rest_framework.exceptions import ValidationError
from flowback.common.services import get_object, model_update
from flowback.group.models import GroupUserDelegatePool
from flowback.poll.models import Poll, PollProposal, PollVoting, PollVotingTypeRanking
from flowback.group.selectors import group_user_permissions
from datetime import datetime


def poll_create(*, user_id: int, group_id: int,
                title: str,
                description: str,
                start_date: datetime,
                end_date: datetime,
                poll_type: int,
                tag: int,
                dynamic: bool
                ) -> Poll:
    group_user = group_user_permissions(user=user_id, group=group_id, permissions=['create_poll', 'admin'])
    poll = Poll(created_by=group_user, title=title, description=description,
                start_date=start_date, end_date=end_date,
                poll_type=poll_type, tag_id=tag, dynamic=dynamic)
    poll.full_clean()
    poll.save()

    return poll


def poll_update(*, user_id: int, group_id: int, poll_id: int, data) -> Poll:
    group_user = group_user_permissions(user=user_id, group=group_id)
    poll = get_object(Poll, id=poll_id)

    if not poll.created_by == group_user or not group_user.is_admin:
        raise ValidationError('Permission denied')

    non_side_effect_fields = ['title', 'description']

    poll, has_updated = model_update(instance=poll,
                                     fields=non_side_effect_fields,
                                     data=data)

    return poll


def poll_delete(*, user_id: int, group_id: int, poll_id: int) -> None:
    group_user = group_user_permissions(user=user_id, group=group_id)
    poll = get_object(Poll, id=poll_id)

    if not poll.created_by == group_user or not group_user.is_admin:
        raise ValidationError('Permission denied')

    if poll.created_by == group_user and poll.finished:
        raise ValidationError('Only group admins (or above) can delete finished polls')

    poll.delete()


def poll_proposal_create(*, user_id: int, group_id: int, poll_id: int, title: str, description: str) -> PollProposal:
    group_user = group_user_permissions(user=user_id, group=group_id)
    poll = get_object(Poll, id=poll_id)

    proposal = PollProposal(created_by=group_user, poll=poll, title=title, description=description)
    proposal.full_clean()
    proposal.save()

    return proposal


def poll_proposal_delete(*, user_id: int, group_id: int, proposal_id: int) -> None:
    group_user = group_user_permissions(user=user_id, group=group_id)
    proposal = get_object(PollProposal, id=proposal_id)
    poll = proposal.poll

    if not proposal.created_by == group_user:
        raise ValidationError('Permission denied')

    if poll.finished:
        raise ValidationError('Only site administrators and above can delete proposals after the poll is finished.')

    proposal.delete()


def poll_proposal_vote_update(*, user_id: int, group_id: int, poll_id: int, data) -> None:
    group_user = group_user_permissions(user=user_id, group=group_id, permissions=['allow_vote', 'admin'])
    poll = get_object(Poll, id=poll_id)

    if poll.poll_type == Poll.PollType.RANKING:
        proposals = poll.pollproposal_set.filter(id__in=[x for x in data]).all()
        if len(proposals) != len(data):
            raise ValidationError('Not all proposals are available to vote for')

        poll_vote, created = PollVoting.objects.get_or_create(created_by=group_user, poll=poll)
        poll_vote_ranking = [PollVotingTypeRanking(author=poll_vote,
                                                   proposal_id=proposal,
                                                   priority=priority) for priority, proposal in enumerate(data)]
        PollVotingTypeRanking.objects.filter(author=poll_vote).delete()
        PollVotingTypeRanking.objects.bulk_create(poll_vote_ranking)

    else:
        raise ValidationError('Unknown poll type')


def poll_proposal_vote_count(*, poll_id: int) -> None:
    poll = get_object(Poll, id=poll_id)

    if poll.poll_type == Poll.PollType.RANKING:
        if poll.tag:
            mandate = GroupUserDelegatePool.objects.filter(polldelegatevoting__poll=poll).aggregate(
                mandate=Sum(Count('groupuserdelegator',
                                  filter=~Q(delegator__pollvoting__poll=poll) &
                                  Q(tags__in=[poll.tag]))))['mandate']

            # Count mandate for each delegate, multiply it by score
            delegate_votes = PollVotingTypeRanking.objects.filter(author_delegate__poll=poll).values('id').annotate(
                score=F('priority') *
                Count('author_delegate__created_by__groupuserdelegator',
                      filter=~Q(delegator__pollvoting__poll=poll) &
                      Q(tags__in=[poll.tag])))

            # Set score to the same as priority for user votes
            user_votes = PollVotingTypeRanking.objects.filter(author__poll=poll
                                                              ).values('id').annotate(score=F('priority'))

            PollVotingTypeRanking.objects.bulk_update(delegate_votes | user_votes, fields=('score',))

            # Update scores on each proposal, Summarize both regular votes and delegate votes
            proposals = PollProposal.objects.filter(poll_id=poll_id).values('id') \
                .annotate(score=Sum('pollvoting__pollvotingtyperanking__priority') +
                          Sum('polldelegatevoting__pollvotingtyperanking__score'))

            PollProposal.objects.bulk_update(proposals, fields=('score',))

            poll.score = mandate + len(user_votes.distinct().count())
            poll.save()


def poll_finish(*, poll_id: int) -> None:
    poll = get_object(Poll, id=poll_id)

    if poll.finished:
        raise ValidationError("Poll is already finished")

    poll_proposal_vote_count(poll_id=poll_id)
    poll.finished = True
    poll.result = True
    poll.save()


def poll_refresh(*, poll_id: int) -> None:
    poll = get_object(Poll, id=poll_id)

    if not poll.dynamic:
        raise ValidationError("Attempted to refresh a poll that doesn't allow live update")

    if poll.finished:
        raise ValidationError("Attempted to refresh a poll that's already finished")

    poll_proposal_vote_count(poll_id=poll_id)


# TODO setup celery
def poll_refresh_cheap(*, poll_id: int) -> None:
    poll = get_object(Poll, id=poll_id)

    if poll.finished and not poll.result or poll.dynamic:
        poll_proposal_vote_count(poll_id=poll_id)
