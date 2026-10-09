from django.contrib.auth import login
from django.shortcuts import redirect, render
from django.views import View
from django.urls import reverse_lazy
from .forms import SignUpForm


class SignUpView(View):
    """
    Handles user registration and immediately signs the user in upon success,
    redirecting directly to their 'My Boards' dashboard.
    """
    template_name = "accounts/signup.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("boards:my_boards")
        form = SignUpForm()
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        if request.user.is_authenticated:
            return redirect("boards:my_boards")
        form = SignUpForm(request.POST)
        if form.is_valid():
            user = form.save()
            # Immediately log in the newly registered user
            login(request, user)
            return redirect("boards:my_boards")
        return render(request, self.template_name, {"form": form})
