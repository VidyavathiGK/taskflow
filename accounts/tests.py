from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

User = get_user_model()


class AccountsTests(TestCase):
    def test_custom_user_creation_with_avatar_color(self):
        user = User.objects.create_user(
            username="testuser",
            password="testpass123",
            avatar_color="#4F46E5"
        )
        self.assertEqual(user.username, "testuser")
        self.assertEqual(user.avatar_color, "#4F46E5")
        self.assertEqual(user.initials, "T")

    def test_signup_view_registers_and_redirects(self):
        signup_url = reverse("accounts:signup")
        response = self.client.post(signup_url, {
            "username": "newuser",
            "email": "newuser@example.com",
            "avatar_color": "#10B981",
            "password1": "ComplexPass123!",
            "password2": "ComplexPass123!",
        })
        self.assertRedirects(response, reverse("boards:my_boards"))
        self.assertTrue(User.objects.filter(username="newuser").exists())

    def test_login_and_logout_flow(self):
        user = User.objects.create_user(username="loginuser", password="password123")
        login_url = reverse("accounts:login")
        response = self.client.post(login_url, {
            "username": "loginuser",
            "password": "password123",
        })
        self.assertRedirects(response, reverse("boards:my_boards"))

        logout_url = reverse("accounts:logout")
        logout_resp = self.client.post(logout_url)
        self.assertRedirects(logout_resp, reverse("accounts:login"))
