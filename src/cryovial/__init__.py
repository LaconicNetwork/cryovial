"""Cryovial — host-resident deploy service for container clusters.

Receives deploy signals (webhook, image-watcher) and pushes images
into clusters via docker pull → kind load → laconic-so restart.
"""
