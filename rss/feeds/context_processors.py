"""
Context processors for feeds app.
"""
from .models import Project


def current_project(request):
    """Add current project to context."""
    # Get project from session or default to first active project
    project_id = request.session.get('current_project_id')
    
    if project_id:
        try:
            project = Project.objects.get(id=project_id, active=True)
        except Project.DoesNotExist:
            project = Project.objects.filter(active=True).first()
            if project:
                request.session['current_project_id'] = project.id
    else:
        project = Project.objects.filter(active=True).first()
        if project:
            request.session['current_project_id'] = project.id
    
    # Get all projects for switcher
    all_projects = Project.objects.filter(active=True)
    
    return {
        'current_project': project,
        'all_projects': all_projects,
    }