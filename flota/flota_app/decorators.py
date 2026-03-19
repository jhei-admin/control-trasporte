from functools import wraps
from django.shortcuts import redirect

def empresa_required(view_func):
    @wraps(view_func)
    def wrapper(request, *args, **kwargs):
        empresa = getattr(request, "empresa", None)

        if not empresa:
            return redirect("login")

        return view_func(request, *args, **kwargs)

    return wrapper