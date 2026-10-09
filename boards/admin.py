from django.contrib import admin
from .models import Board, BoardList, BoardMembership, Card


class BoardMembershipInline(admin.TabularInline):
    model = BoardMembership
    extra = 1
    fields = ("user", "role", "joined_at")
    readonly_fields = ("joined_at",)


class BoardListInline(admin.TabularInline):
    model = BoardList
    extra = 1
    fields = ("title", "position", "created_at")
    readonly_fields = ("created_at",)


class CardInline(admin.TabularInline):
    model = Card
    extra = 1
    fields = ("title", "position", "created_by", "created_at")
    readonly_fields = ("created_at",)


@admin.register(Board)
class BoardAdmin(admin.ModelAdmin):
    list_display = ("title", "owner", "created_at", "updated_at", "member_count", "list_count")
    list_filter = ("created_at", "updated_at")
    search_fields = ("title", "description", "owner__username", "owner__email")
    inlines = [BoardMembershipInline, BoardListInline]

    @admin.display(description="Members")
    def member_count(self, obj):
        return obj.memberships.count()

    @admin.display(description="Lists")
    def list_count(self, obj):
        return obj.lists.count()


@admin.register(BoardMembership)
class BoardMembershipAdmin(admin.ModelAdmin):
    list_display = ("board", "user", "role", "joined_at")
    list_filter = ("role", "joined_at")
    search_fields = ("board__title", "user__username", "user__email")


@admin.register(BoardList)
class BoardListAdmin(admin.ModelAdmin):
    list_display = ("title", "board", "position", "card_count", "created_at")
    list_filter = ("board", "created_at")
    search_fields = ("title", "board__title")
    inlines = [CardInline]

    @admin.display(description="Cards")
    def card_count(self, obj):
        return obj.cards.count()


@admin.register(Card)
class CardAdmin(admin.ModelAdmin):
    list_display = ("title", "board_list", "get_board", "position", "created_by", "created_at")
    list_filter = ("board_list__board", "created_at")
    search_fields = ("title", "description", "created_by__username")

    @admin.display(description="Board")
    def get_board(self, obj):
        return obj.board_list.board.title
