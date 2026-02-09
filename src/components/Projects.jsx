import React from 'react';
import { motion } from 'framer-motion';
import { ExternalLink, Github } from 'lucide-react';

const projects = [
    {
        title: "E-Commerce Dashboard",
        description: "A comprehensive dashboard for managing products, orders, and analytics. Built with React and Recharts.",
        tags: ["React", "Tailwind", "Recharts"],
        image: "https://images.unsplash.com/photo-1460925895917-afdab827c52f?q=80&w=2426&auto=format&fit=crop",
        links: { demo: "#", github: "#" }
    },
    {
        title: "Social Media App",
        description: "Real-time social platform with chat, notifications, and media sharing features.",
        tags: ["Next.js", "Socket.io", "MongoDB"],
        image: "https://images.unsplash.com/photo-1611162617474-5b21e879e113?q=80&w=2574&auto=format&fit=crop",
        links: { demo: "#", github: "#" }
    },
    {
        title: "AI Image Generator",
        description: "SaaS application that uses AI to generate unique images based on text prompts.",
        tags: ["React", "OpenAI API", "Stripe"],
        image: "https://images.unsplash.com/photo-1620712943543-bcc4688e7485?q=80&w=2565&auto=format&fit=crop",
        links: { demo: "#", github: "#" }
    }
];

const Projects = () => {
    return (
        <section id="projects" className="py-20 bg-neutral-950">
            <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
                <motion.div
                    initial={{ opacity: 0, y: 20 }}
                    whileInView={{ opacity: 1, y: 0 }}
                    transition={{ duration: 0.6 }}
                    viewport={{ once: true }}
                    className="mb-16"
                >
                    <h2 className="text-sm font-bold tracking-widest text-blue-500 uppercase mb-2">My Work</h2>
                    <h3 className="text-3xl md:text-5xl font-['Space_Grotesk'] font-bold text-white">
                        Recent <span className="text-purple-500">Projects</span>
                    </h3>
                </motion.div>

                <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                    {projects.map((project, index) => (
                        <motion.div
                            key={index}
                            initial={{ opacity: 0, y: 20 }}
                            whileInView={{ opacity: 1, y: 0 }}
                            transition={{ delay: index * 0.1, duration: 0.5 }}
                            viewport={{ once: true }}
                            className="group bg-neutral-900 border border-neutral-800 rounded-xl overflow-hidden hover:border-blue-500/50 transition-all duration-300"
                        >
                            <div className="relative h-48 overflow-hidden">
                                <div className="absolute inset-0 bg-neutral-950/20 group-hover:bg-transparent transition-colors z-10" />
                                <img
                                    src={project.image}
                                    alt={project.title}
                                    className="w-full h-full object-cover transform group-hover:scale-110 transition-transform duration-500"
                                />
                            </div>

                            <div className="p-6">
                                <h4 className="text-xl font-bold text-white mb-2 group-hover:text-blue-400 transition-colors">
                                    {project.title}
                                </h4>
                                <p className="text-gray-400 text-sm mb-4 line-clamp-3">
                                    {project.description}
                                </p>

                                <div className="flex flex-wrap gap-2 mb-6">
                                    {project.tags.map(tag => (
                                        <span key={tag} className="text-xs font-medium px-2 py-1 bg-neutral-800 text-gray-300 rounded">
                                            {tag}
                                        </span>
                                    ))}
                                </div>

                                <div className="flex items-center gap-4">
                                    <a href={project.links.github} className="flex items-center gap-1 text-sm text-gray-400 hover:text-white transition-colors">
                                        <Github size={16} /> Code
                                    </a>
                                    <a href={project.links.demo} className="flex items-center gap-1 text-sm text-blue-400 hover:text-blue-300 transition-colors">
                                        <ExternalLink size={16} /> Live Demo
                                    </a>
                                </div>
                            </div>
                        </motion.div>
                    ))}
                </div>
            </div>
        </section>
    );
};

export default Projects;
