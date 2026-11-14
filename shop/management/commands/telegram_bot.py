"""Listen for buyer status checks and manager connect links."""
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

from shop.telegram import poll_forever


class Command(BaseCommand):
    help = 'Слушать Telegram: подключение менеджеров и проверка статуса заказа.'

    def handle(self, *args, **options):
        if not settings.TELEGRAM_BOT_TOKEN:
            raise CommandError('Укажите TELEGRAM_BOT_TOKEN.')
        self.stdout.write('Бот слушает личные сообщения. Остановка: Ctrl+C.')
        poll_forever()
