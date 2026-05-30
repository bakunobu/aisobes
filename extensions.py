"""Shared Flask extension instances.

Defined in their own module so that both :mod:`app` and :mod:`models`
import the *same* objects. This avoids the ``__main__`` vs ``app`` double-import
trap (which would otherwise create two ``SQLAlchemy`` instances and trigger
"The current Flask app is not registered with this 'SQLAlchemy' instance").
"""

from flask_sqlalchemy import SQLAlchemy

# Single, shared SQLAlchemy instance for the whole application.
db = SQLAlchemy()
