from django.core.exceptions import ObjectDoesNotExist
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from django.contrib.auth.hashers import check_password

from .models import UserWallet, TeamWallet, OrgWallet


@api_view(["POST"])
def send_funds(request):
    sender_id = request.data.get('sender_id')
    receiver_id = request.data.get('receiver_id')
    recipient_type = request.data.get('recipient_type')  # 'user', 'team', or 'org'
    wallet_pin = request.data.get('wallet_pin')
    amount = request.data.get('amount')

    if not all([sender_id, receiver_id, recipient_type, wallet_pin, amount]):
        return Response({'error': 'All fields are required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        sender_wallet = UserWallet.objects.get(user_id=sender_id)

        if recipient_type == 'user':
            recipient_wallet = UserWallet.objects.get(user_wallet_id=receiver_id)
        elif recipient_type == 'team':
            recipient_wallet = TeamWallet.objects.get(team_wallet_id=receiver_id)
        elif recipient_type == 'org':
            recipient_wallet = OrgWallet.objects.get(org_wallet_id=receiver_id)
        else:
            return Response({'error': 'Invalid recipient type'}, status=status.HTTP_400_BAD_REQUEST)

        if not sender_wallet.pin_hash or not check_password(str(wallet_pin), sender_wallet.pin_hash):
            return Response({'error': 'Invalid wallet pin'}, status=status.HTTP_400_BAD_REQUEST)

        if sender_wallet.wallet_balance < amount:
            return Response({'error': 'Insufficient funds'}, status=status.HTTP_400_BAD_REQUEST)

        sender_wallet.wallet_balance -= amount
        recipient_wallet.wallet_balance += amount

        sender_wallet.save()
        recipient_wallet.save()

        return Response({'success': 'Transfer successful'}, status=status.HTTP_200_OK)

    except ObjectDoesNotExist:
        return Response({'error': 'Sender or recipient wallet not found'}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
